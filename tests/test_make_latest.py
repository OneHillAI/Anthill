"""A stable release starts as a pre-release and is made latest only when it is complete (spec: beta-release-lane R11).

The Release workflow publishes a new stable release minutes before the app files exist, so "latest" briefly had an
installer package and no app download or update feed (v1.0.1 needed a manual fix). scripts/make_latest.py decides,
from what GitHub reports, whether a release is safe to make latest. These tests pin its rules, and the narrowness of
the "Make latest" workflow that uses it. No network.
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_latest", ROOT / "scripts/make_latest.py")
ml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml)

TAG, VER = "v1.0.2", "1.0.2"


def _release(**over):
    assets = [{"name": n, "size": 1000, "state": "uploaded"} for n in ml.required_assets(VER)]
    base = {"tag_name": TAG, "draft": False, "prerelease": True, "assets": assets}
    base.update(over)
    return base


def _feed(**over):
    feed = {
        "version": VER,
        "platforms": {
            "darwin-aarch64": {
                "url": f"https://github.com/OneHillAI/Anthill/releases/download/{TAG}/Anthill_aarch64.app.tar.gz",
                "signature": "c2ln",
            }
        },
    }
    feed.update(over)
    return feed


def test_a_complete_pre_release_is_safe_to_make_latest():
    assert ml.check(TAG, _release(), _feed())


@pytest.mark.parametrize("tag", ["v1.0.2-rc.1", "1.0.2", "v1.0", "main", "v1.0.2x"])
def test_only_a_stable_version_tag_is_accepted(tag):
    with pytest.raises(ml.Refused):
        ml.check(tag, _release(), _feed())


def test_the_six_files_are_all_required():
    for name in ml.required_assets(VER):
        release = _release()
        release["assets"] = [a for a in release["assets"] if a["name"] != name]
        with pytest.raises(ml.Refused, match="missing file"):
            ml.check(TAG, release, _feed())


def test_a_half_uploaded_or_empty_file_is_refused():
    release = _release()
    release["assets"][0]["state"] = "starter"
    with pytest.raises(ml.Refused, match="not fully uploaded"):
        ml.check(TAG, release, _feed())
    release = _release()
    release["assets"][1]["size"] = 0
    with pytest.raises(ml.Refused, match="not fully uploaded"):
        ml.check(TAG, release, _feed())


def test_a_draft_or_an_already_full_release_is_refused():
    with pytest.raises(ml.Refused, match="draft"):
        ml.check(TAG, _release(draft=True), _feed())
    with pytest.raises(ml.Refused, match="already a full release"):
        ml.check(TAG, _release(prerelease=False), _feed())


def test_the_release_must_be_the_one_asked_for():
    with pytest.raises(ml.Refused, match=re.escape("not v1.0.2")):
        ml.check(TAG, _release(tag_name="v1.0.1"), _feed())


def test_the_update_feed_must_name_this_version_and_this_releases_bundle():
    with pytest.raises(ml.Refused, match="update feed says version"):
        ml.check(TAG, _release(), _feed(version="1.0.1"))
    stale = _feed()
    stale["platforms"]["darwin-aarch64"]["url"] = (
        "https://github.com/OneHillAI/Anthill/releases/download/v1.0.1/Anthill_aarch64.app.tar.gz"
    )
    with pytest.raises(ml.Refused, match="does not point at this release"):
        ml.check(TAG, _release(), stale)
    unsigned = _feed()
    unsigned["platforms"]["darwin-aarch64"]["signature"] = ""
    with pytest.raises(ml.Refused, match="no signature"):
        ml.check(TAG, _release(), unsigned)
    with pytest.raises(ml.Refused, match="no platform"):
        ml.check(TAG, _release(), _feed(platforms={}))


def test_both_dmg_copies_must_match_in_size():
    release = _release()
    next(a for a in release["assets"] if a["name"] == "Anthill.dmg")["size"] = 999
    with pytest.raises(ml.Refused, match="differ in size"):
        ml.check(TAG, release, _feed())


def _run(name, status="completed", conclusion="success", at="2026-10-04T10:00:00Z", rid=1):
    return {"name": name, "status": status, "conclusion": conclusion, "created_at": at, "id": rid}


def test_both_release_builds_must_have_finished_green():
    ok = [_run("Release"), _run("Desktop release (Tauri auto-update)")]
    assert ml.runs_green(ok) and ml.runs_green({"workflow_runs": ok})
    with pytest.raises(ml.Refused, match="Desktop release"):
        ml.runs_green([_run("Release")])
    with pytest.raises(ml.Refused, match="in_progress"):
        ml.runs_green(
            [_run("Release"), _run("Desktop release (Tauri auto-update)", "in_progress", None)]
        )
    with pytest.raises(ml.Refused, match="failure"):
        ml.runs_green(
            [_run("Release"), _run("Desktop release (Tauri auto-update)", conclusion="failure")]
        )


def test_a_later_green_rerun_replaces_an_earlier_red_run():
    runs = [
        _run("Release"),
        _run(
            "Desktop release (Tauri auto-update)",
            conclusion="failure",
            at="2026-10-04T10:00:00Z",
            rid=1,
        ),
        _run("Desktop release (Tauri auto-update)", at="2026-10-04T10:30:00Z", rid=2),
    ]
    assert ml.runs_green(runs)


def test_the_cli_exit_codes(tmp_path):
    import json

    rel, feed, runs = tmp_path / "r.json", tmp_path / "f.json", tmp_path / "x.json"
    rel.write_text(json.dumps(_release()))
    feed.write_text(json.dumps(_feed()))
    runs.write_text(json.dumps([_run("Release"), _run("Desktop release (Tauri auto-update)")]))
    assert ml.main(["check", TAG, str(rel), str(feed)]) == 0
    assert ml.main(["check", "v9.9.9", str(rel), str(feed)]) == 1
    assert ml.main(["runs", str(runs)]) == 0
    runs.write_text("[]")
    assert ml.main(["runs", str(runs)]) == 1


# --- the workflows -----------------------------------------------------------------------------------------


def _wf(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_new_stable_releases_start_as_pre_releases():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    desktop = (ROOT / ".github/workflows/desktop-release.yml").read_text()
    assert re.search(r"(?m)^\s+prerelease: true$", release)
    assert re.search(r"(?m)^\s+make_latest: false$", release)
    assert re.search(r"(?m)^\s+prerelease: true$", desktop)
    assert "prerelease: false" not in release + desktop


def test_make_latest_is_hand_started_defaults_to_a_dry_run_and_runs_from_main_only():
    wf = _wf("make-latest.yml")
    on = wf.get("on", wf.get(True))
    assert list(on) == ["workflow_dispatch"]
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert on["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["make-latest"]["steps"]
    assert "refs/heads/main" in steps[0]["run"]


def test_make_latest_checks_everything_before_it_changes_anything():
    steps = _wf("make-latest.yml")["jobs"]["make-latest"]["steps"]
    names = [s.get("name", "") for s in steps]
    change = next(i for i, s in enumerate(steps) if "gh release edit" in s.get("run", ""))
    for needle in ("release owner", "release is complete", "builds finished"):
        assert any(needle in n for n in names[:change]), needle
    assert steps[change]["if"] == "${{ inputs.dry_run == false }}"
    assert "--prerelease=false --latest" in steps[change]["run"]
    owner = next(s for s in steps if "is-owner" in s.get("run", ""))
    assert ".github/release-owners.txt" in owner["run"]


def test_make_latest_never_runs_code_from_a_pull_request():
    text = (ROOT / ".github/workflows/make-latest.yml").read_text()
    assert "pull_request" not in text and "github.event.pull_request" not in text
    assert re.search(r"actions/checkout@[0-9a-f]{40}", text)
