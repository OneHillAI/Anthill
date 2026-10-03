"""The beta release lane: a beta can never reach the live app by accident.

Pins, with no network and no build:

- the version rules for Cut Beta (X.Y.Z-rc.N, base above the live release, N exactly one more than the last);
- the helper that patches a beta's version into the checkout, and the "required checks are green" check;
- that today's two stable release workflows only fire on a stable tag (a beta tag must not publish live);
- that Cut Beta is started by hand, from main only, builds with the beta identity, and only ever publishes
  a pre-release;
- that the beta app has its own name, bundle id, update feed and data folder, and the live app's folder is
  unchanged.
"""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = _load("scripts/beta_release.py", "beta_release")


# --- version rules ----------------------------------------------------------------------------------


def test_the_first_beta_of_a_new_version_is_rc_1():
    assert br.check("1.1.0-rc.1", "1.0.0", ["v1.0.0"])


def test_the_next_beta_is_exactly_one_more_than_the_last():
    tags = ["v1.0.0", "v1.1.0-rc.1"]
    assert br.check("1.1.0-rc.2", "1.0.0", tags)
    for wrong in ("1.1.0-rc.3", "1.1.0-rc.1"):
        with pytest.raises(br.Refused):
            br.check(wrong, "1.0.0", tags)


@pytest.mark.parametrize(
    "version",
    [
        "1.1.0",
        "v1.1.0-rc.1",
        "1.1-rc.1",
        "1.1.0-rc",
        "1.1.0-rc.0",
        "1.1.0-beta.1",
        "1.1.0-rc.1-x",
        " ",
    ],
)
def test_a_malformed_beta_version_is_refused(version):
    with pytest.raises(br.Refused):
        br.check(version, "1.0.0", ["v1.0.0"])


def test_a_beta_must_be_for_a_version_above_the_live_release():
    for version in ("1.0.0-rc.1", "0.9.0-rc.1"):
        with pytest.raises(br.Refused):
            br.check(version, "1.0.0", ["v1.0.0"])
    assert br.check("1.0.1-rc.1", "1.0.0", ["v1.0.0"])


def test_a_beta_for_an_already_released_version_is_refused():
    with pytest.raises(br.Refused):
        br.check("1.1.0-rc.1", "1.0.0", ["v1.0.0", "v1.1.0"])


def test_rc_numbers_are_per_base_version():
    tags = ["v1.0.0", "v1.1.0-rc.1", "v1.1.0-rc.2"]
    assert br.check("1.2.0-rc.1", "1.0.0", tags)  # a new base starts again at 1


def test_the_cli_exit_codes(tmp_path):
    assert (
        br.main(["check", "--version", "1.1.0-rc.1", "--stable", "1.0.0", "--tags", "v1.0.0"]) == 0
    )
    assert br.main(["check", "--version", "1.1.0", "--stable", "1.0.0", "--tags", "v1.0.0"]) == 1


# --- patching the beta's version into the checkout --------------------------------------------------


def _tree(tmp_path):
    for rel in ("src-tauri/tauri.conf.json", "src-tauri/Cargo.toml", "anthill/__init__.py"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    return tmp_path


def test_apply_sets_the_rc_version_in_all_three_files(tmp_path):
    root = _tree(tmp_path)
    br.apply_version("1.1.0-rc.2", root)
    assert json.loads((root / "src-tauri/tauri.conf.json").read_text())["version"] == "1.1.0-rc.2"
    assert re.search(
        r'(?m)^version = "1\.1\.0-rc\.2"$', (root / "src-tauri/Cargo.toml").read_text()
    )
    assert '__version__ = "1.1.0-rc.2"' in (root / "anthill/__init__.py").read_text()
    assert br.stable_version(root) == "1.1.0-rc.2"


def test_apply_leaves_everything_else_alone(tmp_path):
    root = _tree(tmp_path)
    before = (root / "src-tauri/tauri.conf.json").read_text()
    br.apply_version("1.1.0-rc.1", root)
    after = (root / "src-tauri/tauri.conf.json").read_text()
    changed = [
        (a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b
    ]
    assert len(changed) == 1 and '"version"' in changed[0][0]


def test_apply_refuses_a_stable_version(tmp_path):
    with pytest.raises(br.Refused):
        br.apply_version("1.1.0", _tree(tmp_path))


def test_the_real_stable_version_is_readable():
    assert re.fullmatch(r"\d+\.\d+\.\d+", br.stable_version(ROOT))


# --- required checks are green ----------------------------------------------------------------------


def _run(name, conclusion="success", status="completed", started="2026-10-03T10:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started}


def _all_green():
    return [_run(n) for n in br.REQUIRED_CHECKS]


def test_ci_green_passes_when_every_required_check_succeeded():
    assert br.ci_green({"check_runs": _all_green()})
    assert br.ci_green([*_all_green(), _run("CodeQL", "neutral")])  # unrelated checks do not matter


@pytest.mark.parametrize("bad", ["failure", "cancelled", "timed_out"])
def test_ci_green_refuses_a_failed_required_check(bad):
    runs = _all_green()
    runs[1] = _run("test (3.10)", bad)
    with pytest.raises(br.Refused):
        br.ci_green(runs)


def test_ci_green_refuses_a_missing_or_unfinished_required_check():
    with pytest.raises(br.Refused):
        br.ci_green([r for r in _all_green() if r["name"] != "test (3.12)"])
    runs = _all_green()
    runs[0] = _run("lint", None, "in_progress")
    with pytest.raises(br.Refused):
        br.ci_green(runs)


def test_ci_green_uses_the_latest_run_of_each_check():
    older_fail_newer_pass = [*_all_green(), _run("lint", "failure", started="2026-10-03T09:00:00Z")]
    assert br.ci_green(older_fail_newer_pass)
    older_pass_newer_fail = [
        *[r for r in _all_green() if r["name"] != "lint"],
        _run("lint", "success", started="2026-10-03T09:00:00Z"),
        _run("lint", "failure", started="2026-10-03T11:00:00Z"),
    ]
    with pytest.raises(br.Refused):
        br.ci_green(older_pass_newer_fail)


# --- only release files changed since the beta (used by Promote) ------------------------------------


def test_only_release_files_may_change_between_the_beta_and_the_release():
    assert br.release_files_only(
        [
            "CHANGELOG.md",
            "pyproject.toml",
            "anthill/__init__.py",
            "changelog.d/57.added.md",
            "src-tauri/Cargo.toml",
        ]
    )
    for bad in (
        "anthill/web/app.py",
        "docs/guide.md",
        "tests/test_x.py",
        ".github/workflows/ci.yml",
    ):
        with pytest.raises(br.Refused):
            br.release_files_only(["CHANGELOG.md", bad])
    assert br.release_files_only([])  # nothing changed at all is fine


# --- a beta tag must never start a live release -----------------------------------------------------


def _glob_to_regex(pattern):
    """GitHub's tag filter semantics for the characters used here: [..] classes, + (one or more of the
    previous token), * (any run without a slash), ? (one char); everything else is literal. The whole tag
    name must match."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[":
            j = pattern.index("]", i)
            out.append(pattern[i : j + 1])
            i = j
        elif c == "+":
            out.append("+")
        elif c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def _tag_patterns(workflow):
    text = (ROOT / ".github/workflows" / workflow).read_text()
    block = text.split("\njobs:")[0]
    m = re.search(r"(?m)^\s*tags:\s*\[(.*)\]\s*$", block)
    assert m, f"{workflow} has no tag filter"
    return re.findall(r'"([^"]+)"', m.group(1))


@pytest.mark.parametrize("workflow", ["release.yml", "desktop-release.yml"])
def test_the_stable_release_workflows_ignore_beta_tags(workflow):
    rxs = [_glob_to_regex(p) for p in _tag_patterns(workflow)]
    fires = lambda tag: any(r.fullmatch(tag) for r in rxs)  # noqa: E731
    for stable in ("v1.1.0", "v1.10.20", "v2.0.0"):
        assert fires(stable), stable
    for not_stable in (
        "v1.1.0-rc.1",
        "v1.1.0-rc.12",
        "v1.1.0-beta",
        "v1.1",
        "v1.1.0.1",
        "beta-channel",
        "vfoo",
    ):
        assert not fires(not_stable), not_stable


# --- Cut Beta ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def beta_wf():
    return (ROOT / ".github/workflows/desktop-beta.yml").read_text()


def test_cut_beta_is_started_by_hand_only(beta_wf):
    on = beta_wf.split("\npermissions:")[0]
    assert "workflow_dispatch:" in on
    for trigger in ("push:", "pull_request", "schedule:", "tags:", "workflow_run"):
        assert trigger not in on, trigger
    assert "version:" in on and "dry_run:" in on


def test_cut_beta_refuses_anything_but_main_and_a_red_commit(beta_wf):
    assert "refs/heads/main" in beta_wf and "Cut Beta only runs from main" in beta_wf
    assert "scripts/beta_release.py check" in beta_wf
    assert "scripts/beta_release.py ci-green" in beta_wf
    assert "scripts/beta_release.py apply" in beta_wf


def test_cut_beta_only_ever_publishes_a_pre_release_with_the_beta_identity(beta_wf):
    assert re.search(r"(?m)^\s+prerelease: true$", beta_wf)
    assert "prerelease: false" not in beta_wf
    assert "--config src-tauri/tauri.beta.conf.json" in beta_wf
    assert (
        "--prerelease --latest=false" in beta_wf
    )  # neither the beta nor the feed is ever "latest"
    assert "inputs.dry_run == false" in beta_wf  # a dry run creates no tag, release or feed


def test_cut_beta_never_touches_the_stable_download_or_feed(beta_wf):
    uploads = [ln for ln in beta_wf.splitlines() if "gh release upload" in ln]
    assert uploads, "the beta publishes its files with gh release upload"
    # every upload goes to the beta's own release or the beta feed, never to the live release
    assert all('"$TAG"' in ln or "beta-channel" in ln for ln in uploads), uploads
    assert not any(
        re.search(r"(?<![\w-])Anthill\.dmg", ln) for ln in uploads
    )  # only Anthill-Beta.dmg
    code = "\n".join(ln for ln in beta_wf.splitlines() if not ln.lstrip().startswith("#"))
    assert "releases/latest" not in code  # the live feed address appears nowhere in the workflow


# --- the beta app's own identity --------------------------------------------------------------------


def test_the_beta_app_has_its_own_identity_and_feed():
    stable = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())
    beta = json.loads((ROOT / "src-tauri/tauri.beta.conf.json").read_text())
    assert beta["productName"] == "Anthill Beta" and stable["productName"] == "Anthill"
    assert (
        beta["identifier"] == "org.onehill.anthill.beta"
        and beta["identifier"] != stable["identifier"]
    )
    beta_feed = beta["plugins"]["updater"]["endpoints"]
    assert beta_feed == [
        "https://github.com/OneHillAI/Anthill/releases/download/beta-channel/latest.json"
    ]
    assert beta_feed != stable["plugins"]["updater"]["endpoints"]
    assert (
        "pubkey" not in beta["plugins"]["updater"]
    )  # same signing key: inherited, never overridden
    assert "version" not in beta  # patched into the checkout from the input, never committed


def test_the_live_apps_feed_is_unchanged():
    stable = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())
    assert stable["plugins"]["updater"]["endpoints"] == [
        "https://github.com/OneHillAI/Anthill/releases/latest/download/latest.json"
    ]
    assert stable["identifier"] == "org.onehill.anthill"


# --- the beta's own data folder ---------------------------------------------------------------------


def _data_dir(monkeypatch, tmp_path, env):
    desktop = importlib.import_module("anthill.desktop")
    seen = []
    monkeypatch.setattr(
        desktop,
        "user_data_dir",
        lambda name, appauthor=False: seen.append(name) or str(tmp_path / name),
    )
    if env is None:
        monkeypatch.delenv("ANTHILL_APP_NAME", raising=False)
    else:
        monkeypatch.setenv("ANTHILL_APP_NAME", env)
    return desktop.data_dir(), seen


def test_the_live_apps_data_folder_is_unchanged(monkeypatch, tmp_path):
    for env in (None, "", "   ", "Anthill"):
        path, seen = _data_dir(monkeypatch, tmp_path, env)
        assert seen == ["Anthill"] and path.name == "Anthill", env


def test_the_beta_keeps_its_own_data_folder(monkeypatch, tmp_path):
    path, seen = _data_dir(monkeypatch, tmp_path, "Anthill Beta")
    assert seen == ["Anthill Beta"] and path.name == "Anthill Beta"
    assert path != _data_dir(monkeypatch, tmp_path, None)[0]


# --- a second, independent guard on the stable workflows --------------------------------------------


def _guard_script(workflow):
    import yaml

    doc = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    steps = next(iter(doc["jobs"].values()))["steps"]
    names = [s.get("name") for s in steps]
    assert "Refuse anything but a stable version tag" in names
    idx = names.index("Refuse anything but a stable version tag")
    assert idx <= 1, "the guard must run before any build or publish step"
    return steps[idx]["run"]


@pytest.mark.parametrize("workflow", ["release.yml", "desktop-release.yml"])
def test_the_stable_workflows_refuse_a_beta_tag_even_if_the_filter_let_it_through(workflow):
    script = _guard_script(workflow)

    def passes(tag):
        r = subprocess.run(
            ["bash", "-c", script], env={"GITHUB_REF_NAME": tag, "PATH": "/usr/bin:/bin"}
        )
        return r.returncode == 0

    for stable in ("v1.1.0", "v10.20.30", "v2.0.0"):
        assert passes(stable), stable
    for not_stable in (
        "v1.1.0-rc.1",
        "v1.1.0-rc.12",
        "v1.1.0-beta",
        "v1.1",
        "v1.1.0.1",
        "beta-channel",
        "vfoo",
        "1.1.0",
    ):
        assert not passes(not_stable), not_stable


def test_cut_beta_pins_the_release_tag_to_the_commit_that_was_built(beta_wf):
    assert "commitish: ${{ github.sha }}" in beta_wf


def test_ci_green_breaks_a_start_time_tie_with_the_run_id():
    same_start = "2026-10-03T10:00:00Z"
    rerun_green = [
        *[r for r in _all_green() if r["name"] != "lint"],
        {**_run("lint", "failure", started=same_start), "id": 1},
        {**_run("lint", "success", started=same_start), "id": 2},
    ]
    assert br.ci_green(rerun_green)
    rerun_red = [
        *[r for r in _all_green() if r["name"] != "lint"],
        {**_run("lint", "success", started=same_start), "id": 1},
        {**_run("lint", "failure", started=same_start), "id": 2},
    ]
    with pytest.raises(br.Refused):
        br.ci_green(rerun_red)


# --- the beta cannot reach the live app's keys ------------------------------------------------------


def test_the_apps_secrets_live_in_the_data_folder_not_the_keychain():
    """The beta is isolated from the live app by its own data folder. That only protects the keys while they
    live inside it (they do: secrets.env in the data dir). If code ever starts using the macOS keychain, the
    beta and the live app could end up sharing an item, so the beta's isolation would need revisiting."""
    pattern = re.compile(r"keyring|Keychain|keychain|SecItem|add-generic-password")
    offenders = []
    for base in ("anthill", "src-tauri/src"):
        for path in (ROOT / base).rglob("*"):
            if (
                path.suffix in (".py", ".rs")
                and path.is_file()
                and pattern.search(path.read_text(errors="replace"))
            ):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"keychain use found: {offenders}; revisit the beta's isolation (spec R3)"


# --- no pipe-to-shell installs in the new release workflows -----------------------------------------


@pytest.mark.parametrize("workflow", ["desktop-beta.yml"])
def test_the_new_release_workflows_never_pipe_a_download_into_a_shell(workflow):
    """The security scan blocks a download piped straight into a shell on any line a PR adds, and it is a real
    supply-chain risk in a workflow that holds signing keys. Tools come from commit-pinned actions instead."""
    text = (ROOT / ".github/workflows" / workflow).read_text()
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    offenders = [
        ln.strip() for ln in code if re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", ln)
    ]
    assert not offenders, offenders
    uv = next((ln for ln in code if "setup-uv" in ln), "")
    assert re.search(r"@[0-9a-f]{40}\b", uv), "uv must come from a commit-pinned action"
