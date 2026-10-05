"""Guards on the Windows part of Cut Beta, so the macOS beta stays as it was and the Windows part keeps the beta's rules.

The workflows run only when someone presses a button, so a pull request cannot prove them; these tests pin the
properties that matter (what it may publish, in what order, and that the macOS job is not touched).
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def beta():
    return yaml.safe_load((WORKFLOWS / "desktop-beta.yml").read_text())


@pytest.fixture(scope="module")
def text():
    return (WORKFLOWS / "desktop-beta-windows.yml").read_text()


@pytest.fixture(scope="module")
def win(text):
    return yaml.safe_load(text)


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _on(doc: dict) -> dict:
    return doc.get("on") or doc.get(True)  # YAML reads a bare `on:` key as the boolean True


def test_cut_beta_has_the_macos_job_first_and_one_windows_job_that_calls_the_new_workflow(beta):
    assert list(beta["jobs"]) == ["beta", "windows"]
    job = beta["jobs"]["windows"]
    assert job["needs"] == "beta"  # only after the macOS job has published the beta
    assert job["uses"] == "./.github/workflows/desktop-beta-windows.yml"
    assert job["with"] == {"version": "${{ inputs.version }}", "dry_run": "${{ inputs.dry_run }}"}
    assert job["secrets"] == "inherit"
    assert "runs-on" not in job and "steps" not in job  # all Windows logic lives in the other file


def test_the_windows_beta_is_started_by_the_macos_beta_or_by_hand_only(win):
    on = _on(win)
    assert set(on) == {"workflow_call", "workflow_dispatch"}
    assert on["workflow_call"]["inputs"]["dry_run"]["required"] is True  # the caller must say
    assert (
        on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    )  # by hand it is a dry run first


def test_it_uses_its_own_concurrency_group_not_the_callers(win, beta):
    assert win["concurrency"]["group"] == "cut-beta-windows"
    assert (
        win["concurrency"]["group"] != beta["concurrency"]["group"]
    )  # the same group would wait for itself


def test_it_builds_on_a_windows_runner_and_refuses_anything_but_main(win, text):
    job = win["jobs"]["windows-beta"]
    assert job["runs-on"] == "windows-latest"
    assert "refs/heads/main" in text and "only runs from main" in text
    assert "scripts/beta_release.py ci-green" in text
    assert (
        "scripts/beta_release.py apply" in text
    )  # the beta version is patched in the checkout only


def test_it_only_ever_adds_to_a_pre_release_with_the_beta_identity(text):
    assert re.search(r"(?m)^\s+prerelease: true$", text)
    assert "prerelease: false" not in text
    assert "--config src-tauri/tauri.beta.conf.json" in text
    assert "commitish: ${{ github.sha }}" in text
    assert "inputs.dry_run == false" in text  # a dry run attaches and changes nothing
    assert "--prerelease --latest=false" in text  # the feed is never "latest"


def test_it_never_touches_the_stable_download_or_feed(text):
    uploads = [ln for ln in text.splitlines() if "gh release upload" in ln]
    assert uploads, "the Windows beta publishes its feed files with gh release upload"
    assert all("beta-channel" in ln for ln in uploads), uploads
    assert not any(re.search(r"(?<![\w-])Anthill\.dmg", ln) for ln in uploads)
    assert "releases/latest" not in _code(
        text
    )  # the live feed address appears nowhere in the workflow


def test_the_feed_is_updated_last_so_a_failed_check_never_reaches_testers(win):
    steps = [s["name"] for s in win["jobs"]["windows-beta"]["steps"] if s.get("name")]
    build = steps.index("Build + attach the Windows beta and update artifacts")
    check = steps.index("Install the beta, run it, crash it, close it, uninstall it")
    complete = steps.index("The beta release holds every Windows file")
    feed = steps.index("Publish the Windows beta on the beta feed")
    assert build < check < complete < feed
    assert steps.index("Stop anything left running") > feed  # cleanup runs always, after everything


def test_it_signs_the_updater_bundle_with_the_same_secrets_as_the_macos_beta(win, beta):
    def build_env(job):
        step = next(s for s in job["steps"] if "tauri-action" in s.get("uses", ""))
        return set(step["env"])

    mac = build_env(beta["jobs"]["beta"])
    windows = build_env(win["jobs"]["windows-beta"])
    assert {"TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"} <= windows
    assert windows <= mac  # nothing the macOS beta does not already use (no Apple secrets here)


def test_it_never_pipes_a_download_into_a_shell_and_takes_uv_from_a_pinned_action(text):
    code = _code(text).splitlines()
    offenders = [
        ln.strip() for ln in code if re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", ln)
    ]
    assert not offenders, offenders
    uv = next((ln for ln in code if "setup-uv" in ln), "")
    assert re.search(r"@[0-9a-f]{40}\b", uv), "uv must come from a commit-pinned action"


def test_the_install_check_is_told_the_betas_data_folder(text):
    assert "ANTHILL_CHECK_DATA_FOLDER: Anthill Beta" in text
