"""Guards on the Windows part of the stable release, so the macOS release stays as it was and Windows keeps the rules.

The workflows run only on a tag (or by hand), so a pull request cannot run them; these tests pin what must stay true:
what may be published and where, that a real run builds the tag itself, that nothing is published before the install
check, that the manifest is written last, and that every publishing step needs a tag.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = "windows-release"


@pytest.fixture(scope="module")
def stable():
    return yaml.safe_load((WORKFLOWS / "desktop-release.yml").read_text())


@pytest.fixture(scope="module")
def text():
    return (WORKFLOWS / "desktop-release-windows.yml").read_text()


@pytest.fixture(scope="module")
def win(text):
    return yaml.safe_load(text)


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _on(doc: dict) -> dict:
    return doc.get("on") or doc.get(True)  # YAML reads a bare `on:` key as the boolean True


def _steps(win) -> list[dict]:
    return win["jobs"][RELEASE]["steps"]


def _step(win, name: str) -> dict:
    return next(s for s in _steps(win) if s.get("name") == name)


def _names(win) -> list[str | None]:
    return [s.get("name") for s in _steps(win)]


PUBLISHING = (
    "Upload the installer and its signature to the release",
    "Add the Windows entry to latest.json and check both platforms",
    "Publish the new latest.json",
    "Mark the Windows file as an alpha and name it",
    "Sync the Tauri app version to the tag",
    "The release exists and is still a pre-release",
)


def test_the_stable_release_has_the_macos_job_first_and_one_windows_job_that_calls_the_new_workflow(
    stable,
):
    assert list(stable["jobs"]) == ["desktop", "windows"]
    job = stable["jobs"]["windows"]
    assert job["needs"] == "desktop"  # only after the macOS job has published the release
    assert job["uses"] == "./.github/workflows/desktop-release-windows.yml"
    assert job["with"] == {
        "tag": "${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || '' }}"
    }
    assert job["secrets"] == "inherit"
    assert "runs-on" not in job and "steps" not in job  # all Windows logic lives in the other file


def test_it_is_started_by_the_stable_release_or_by_hand_only_and_defaults_to_a_dry_run(win):
    on = _on(win)
    assert set(on) == {"workflow_call", "workflow_dispatch"}
    for trigger in on.values():
        assert trigger["inputs"]["tag"]["default"] == ""  # no tag: nothing is published


def test_it_uses_its_own_concurrency_group_not_the_callers(win, stable):
    assert win["concurrency"]["group"] == "release-windows"
    assert win["concurrency"]["group"] != (stable.get("concurrency") or {}).get("group")


# --- a real run builds the tag itself -------------------------------------------------------------


def _run_guard(win, name: str, env: dict) -> bool:
    script = _step(win, name)["run"]
    return (
        subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", **env}).returncode == 0
    )


def test_the_stable_tag_guard_runs_right_after_the_checkout_and_refuses_anything_else(win):
    names = _names(win)
    assert names.index("Refuse anything but a stable version tag") <= 1
    assert "checkout" in _steps(win)[0].get("uses", "")
    for stable_tag in ("", "v1.1.0", "v10.20.30"):  # empty is the dry run
        assert _run_guard(win, "Refuse anything but a stable version tag", {"TAG": stable_tag}), (
            stable_tag
        )
    for other in ("v1.1.0-rc.1", "v1.1", "v1.1.0.1", "beta-channel", "1.1.0", "vfoo", "v1.1.0 "):
        assert not _run_guard(win, "Refuse anything but a stable version tag", {"TAG": other}), (
            other
        )


def test_a_real_run_must_start_from_the_tag_it_releases(win):
    names = _names(win)
    guard = "A real run builds the tag itself"
    assert names.index(guard) < names.index("Build the Windows installer and its update signature")
    assert names.index(guard) <= 2, "the check must come before any build, publish or signing step"

    def passes(tag: str, ref: str) -> bool:
        return _run_guard(win, guard, {"TAG": tag, "GITHUB_REF": ref})

    assert passes("v1.2.3", "refs/tags/v1.2.3")  # started from its own tag
    assert passes(
        "", "refs/heads/some-branch"
    )  # a dry run may start from anywhere: it publishes nothing
    assert not passes("v1.2.3", "refs/heads/some-branch")  # a branch someone picked by hand
    assert not passes("v1.2.3", "refs/heads/main")
    assert not passes("v1.2.3", "refs/tags/v1.2.4")  # another tag
    assert not passes("v1.2.3", "refs/tags/v1.2.3-rc.1")
    assert not passes("v1.2.3", "")


# --- nothing is published before the install check, and the manifest is written last ------------------


def test_nothing_is_published_by_the_build_itself(text, win):
    assert (
        "tauri-action" not in text
    )  # an action that publishes while it builds would put files up before the check
    build = _step(win, "Build the Windows installer and its update signature")
    assert "tauri build --bundles nsis" in build["run"]
    assert "gh " not in build["run"]


def test_the_steps_run_in_a_safe_order(win):
    names = _names(win)
    order = [
        "Build the Windows installer and its update signature",
        "Install it, run it, crash it, close it, uninstall it",
        "Upload the installer and its signature to the release",
        "Add the Windows entry to latest.json and check both platforms",
        "Publish the new latest.json",
        "Mark the Windows file as an alpha and name it",
        "Stop anything left running",
    ]
    positions = [names.index(n) for n in order]
    assert positions == sorted(positions), (
        "the install check, then the files, then the manifest, then the notes"
    )


def test_the_merged_manifest_is_checked_before_it_is_published(win):
    merge = _step(win, "Add the Windows entry to latest.json and check both platforms")["run"]
    assert merge.index("windows_release_manifest.py") < merge.index("verify_release.py")
    assert "--platform macos --platform windows" in merge
    publish = _step(win, "Publish the new latest.json")["run"]
    assert publish.count("gh release upload") == 1 and "latest.json" in publish
    assert "gh release upload" not in merge, "the merge step must not publish"


def test_every_publishing_step_needs_a_tag_and_every_build_step_needs_the_key(win):
    for name in PUBLISHING:
        condition = _step(win, name)["if"]
        assert "inputs.tag != ''" in condition and "env.SIGN_KEY != ''" in condition, name
    for name in (
        "Build the anthill-server sidecar",
        "Build the Windows installer and its update signature",
        "Install it, run it, crash it, close it, uninstall it",
    ):
        assert "env.SIGN_KEY != ''" in _step(win, name)["if"], name
    for step in _steps(win):
        if (
            "gh release" in str(step.get("run", ""))
            and step["name"] != "Stop anything left running"
        ):
            assert "inputs.tag != ''" in step.get("if", ""), step[
                "name"
            ]  # no release call without a tag


# --- what it may publish, and where --------------------------------------------------------------


def test_it_never_makes_a_release_the_latest_or_changes_whether_it_is_a_pre_release(text):
    code = _code(text)
    gh_calls = [ln for ln in code.splitlines() if "gh release" in ln]
    assert gh_calls
    assert not any(re.search(r"--latest\b|--prerelease\b|--draft\b", ln) for ln in gh_calls), (
        gh_calls
    )
    assert "make_latest" not in code and "prerelease: false" not in code
    edits = [ln for ln in gh_calls if "gh release edit" in ln]
    assert all("--notes-file" in ln for ln in edits), edits  # the only edit is to the release notes


def test_it_publishes_only_to_the_release_it_was_asked_for(text):
    code = _code(text)
    calls = [ln for ln in code.splitlines() if "gh release upload" in ln or "gh release edit" in ln]
    assert calls and all('"$TAG"' in ln for ln in calls), calls
    assert "beta-channel" not in code and "releases/latest" not in code
    assert not any(
        re.search(r"(?<![\w-])Anthill\.dmg", ln) for ln in calls
    )  # the macOS download is not touched


def test_the_alpha_paragraph_is_found_by_its_own_marker_not_by_its_words(win):
    run = _step(win, "Mark the Windows file as an alpha and name it")["run"]
    marker = "<!-- windows-alpha-note -->"
    assert (
        f'grep -qF "{marker}"' in run
    )  # a fixed string, so nothing a person writes can match it by accident
    assert run.count(marker) == 2 and 'grep -q "Windows alpha"' not in run


def test_it_signs_the_updater_bundle_with_no_secret_the_macos_job_does_not_use(win, stable):
    mac = next(s for s in stable["jobs"]["desktop"]["steps"] if "tauri-action" in s.get("uses", ""))
    build = _step(win, "Build the Windows installer and its update signature")
    assert {"TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"} == set(build["env"])
    assert set(build["env"]) <= set(mac["env"])


def test_it_never_pipes_a_download_into_a_shell_and_takes_uv_from_a_pinned_action(text):
    code = _code(text).splitlines()
    offenders = [
        ln.strip() for ln in code if re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", ln)
    ]
    assert not offenders, offenders
    uv = next((ln for ln in code if "setup-uv" in ln), "")
    assert re.search(r"@[0-9a-f]{40}\b", uv), "uv must come from a commit-pinned action"
