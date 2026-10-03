"""Promote Beta: only an owner, only a beta tested as is, and only behind a second approval.

Pins, with no network and no release:

- who may promote (`.github/release-owners.txt`) and which beta is promotable;
- that the test agent's PASS must be the bot's own report for that exact commit (a human comment cannot stand in);
- that the workflow checks everything before it creates anything, defaults to a dry run, is hand-started from
  main only, and that only the second job, behind the `production` approval, mints the bot token and creates
  the stable tag (which is what starts today's two release workflows).
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "beta_release_promote", ROOT / "scripts/beta_release.py"
)
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

SHA = "a" * 40
HEADER = f"## Test agent - result for `{SHA}`"
BOT = {"login": "github-actions[bot]"}


# --- who may promote ---------------------------------------------------------------------------------


def test_the_real_owner_file_lists_a_release_owner():
    text = (ROOT / ".github/release-owners.txt").read_text()
    assert br.is_owner("welsbach", text)
    assert br.is_owner("WelsBach", text)  # logins are case-insensitive
    with pytest.raises(br.Refused):
        br.is_owner("someone-else", text)


@pytest.mark.parametrize("owners", ["", "\n\n", "# only a comment\n", "   # indented comment\n"])
def test_no_listed_owner_means_nobody_can_promote(owners):
    with pytest.raises(br.Refused):
        br.is_owner("welsbach", owners)


def test_owners_are_one_login_per_line_with_comments_ignored():
    text = "# who may promote\nalice\n  bob  # a comment\n\nCarol\n"
    for ok in ("alice", "bob", "carol"):
        assert br.is_owner(ok, text)
    for no in ("dave", "# who may promote", "a comment", "ali"):
        with pytest.raises(br.Refused):
            br.is_owner(no, text)


# --- which beta is promotable ------------------------------------------------------------------------


def test_a_beta_tag_resolves_to_its_stable_version():
    tags = ["v1.0.0", "v1.1.0-rc.1", "v1.1.0-rc.2"]
    assert br.promotable("v1.1.0-rc.2", tags) == "1.1.0"
    assert br.promotable("v1.1.0-rc.1", tags) == "1.1.0"


@pytest.mark.parametrize(
    "beta", ["1.1.0-rc.1", "v1.1.0", "v1.1.0-beta", "v1.1-rc.1", "", "v1.1.0-rc.9"]
)
def test_a_wrong_or_missing_beta_is_refused(beta):
    with pytest.raises(br.Refused):
        br.promotable(beta, ["v1.0.0", "v1.1.0-rc.1"])


def test_an_already_released_version_is_refused():
    with pytest.raises(br.Refused):
        br.promotable("v1.1.0-rc.1", ["v1.0.0", "v1.1.0-rc.1", "v1.1.0"])


# --- the test agent's verdict (a structured commit status) -------------------------------------------


def _status(state, *, context="asdd/test", who=BOT, at="2026-10-03T10:00:00Z", sid=1):
    return {"id": sid, "context": context, "state": state, "created_at": at, "creator": who}


def test_a_success_status_from_the_bot_for_that_commit_is_accepted():
    assert br.tester_passed([_status("success")], SHA)


@pytest.mark.parametrize("state", ["failure", "error", "pending"])
def test_a_status_that_is_not_success_is_refused(state):
    with pytest.raises(br.Refused):
        br.tester_passed([_status(state)], SHA)


def test_no_verdict_recorded_is_refused():
    with pytest.raises(br.Refused):
        br.tester_passed([], SHA)
    with pytest.raises(br.Refused):
        br.tester_passed(
            [_status("success", context="ci/other")], SHA
        )  # a different check does not count


def test_a_human_cannot_set_the_verdict():
    with pytest.raises(br.Refused):
        br.tester_passed([_status("success", who={"login": "welsbach"})], SHA)


def test_the_newest_verdict_wins():
    older_fail_newer_pass = [
        _status("failure", at="2026-10-03T09:00:00Z", sid=1),
        _status("success", at="2026-10-03T11:00:00Z", sid=2),
    ]
    assert br.tester_passed(older_fail_newer_pass, SHA)
    older_pass_newer_fail = [
        _status("success", at="2026-10-03T09:00:00Z", sid=1),
        _status("failure", at="2026-10-03T11:00:00Z", sid=2),
    ]
    with pytest.raises(br.Refused):
        br.tester_passed(older_pass_newer_fail, SHA)
    same_time = [_status("failure", sid=1), _status("success", sid=2)]  # the run id breaks a tie
    assert br.tester_passed(same_time, SHA)


def test_the_test_workflow_records_the_verdict_promote_reads():
    """The writer and the reader agree on one structured signal: the test workflow sets the `asdd/test` status
    from the agent's own result file, and Promote reads that same status."""
    text = (ROOT / ".github/workflows/asdd-test.yml").read_text()
    assert re.search(r"(?m)^\s+statuses: write", text)
    assert '-f context="asdd/test"' in text
    assert "test-report.py --verdict" in text
    assert (
        "steps.onmain.outcome == 'success'" in text
    )  # only ever for a commit that passed the on-main check
    promote = (ROOT / ".github/workflows/desktop-promote.yml").read_text()
    assert "/statuses" in promote and "tester-pass" in promote


def test_the_verdict_renderer_maps_the_agents_result_to_the_status():
    spec = importlib.util.spec_from_file_location(
        "test_report_verdict", ROOT / ".github/asdd/operate/test-report.py"
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    assert renderer.verdict({"verdict": "pass"}) == "pass"
    assert renderer.verdict({"verdict": " FAIL "}) == "fail"
    for junk in ({}, {"verdict": ""}, {"verdict": "maybe"}, {"verdict": ["pass"]}):
        assert renderer.verdict(junk) == "none"


def test_the_cli_exit_codes(tmp_path):
    cfg = tmp_path / "release-owners.txt"
    cfg.write_text("alice\n")
    assert br.main(["is-owner", "--actor", "alice", "--config", str(cfg)]) == 0
    assert br.main(["is-owner", "--actor", "bob", "--config", str(cfg)]) == 1
    assert br.main(["promotable", "--beta", "v1.1.0-rc.1", "--tags", "v1.0.0\nv1.1.0-rc.1"]) == 0
    assert br.main(["promotable", "--beta", "v1.1.0-rc.1", "--tags", "v1.0.0"]) == 1


# --- only a version bump in the files that could carry behaviour ------------------------------------------


PYPROJECT = '[project]\nname = "anthill"\nversion = "{v}"\ndependencies = [\n    "markitdown[pdf]>=0.1.6,<0.2",\n]\n'


def test_a_version_bump_alone_is_allowed():
    assert br.version_only(PYPROJECT.format(v="1.0.0"), PYPROJECT.format(v="1.1.0"))
    assert br.version_only('__version__ = "1.0.0"\n', '__version__ = "1.1.0"\n')
    assert br.version_only(
        PYPROJECT.format(v="1.0.0"), PYPROJECT.format(v="1.0.0")
    )  # no change at all


@pytest.mark.parametrize(
    "smuggle",
    [
        lambda s: s.replace("]\n", '    "evil-package>=1",\n]\n'),  # a new dependency
        lambda s: s + 'dependencies = ["x"]\n',
        lambda s: s + "import os\n",
        lambda s: s.replace('    "markitdown[pdf]>=0.1.6,<0.2",\n', ""),  # a removed dependency
        lambda s: s.replace('name = "anthill"', 'name = "anthill"\nversion_hack = "1.1.0"'),
        # lines that look like diff headers: the old diff reader skipped these, a whole-file compare cannot
        lambda s: s + "+++ hidden\n",
        lambda s: s + "--- hidden\n",
        lambda s: s.replace('    "markitdown[pdf]>=0.1.6,<0.2",', "-- x"),
    ],
)
def test_anything_besides_the_version_line_is_refused(smuggle):
    new = smuggle(PYPROJECT.format(v="1.1.0"))
    with pytest.raises(br.Refused):
        br.version_only(PYPROJECT.format(v="1.0.0"), new)


def test_only_the_first_version_line_is_exempt():
    """A second version-looking line (a dependency table, say) is real content and cannot be swapped quietly."""
    old = PYPROJECT.format(v="1.0.0") + '[tool.x]\nversion = "2"\n'
    new = PYPROJECT.format(v="1.1.0") + '[tool.x]\nversion = "3"\n'
    with pytest.raises(br.Refused):
        br.version_only(old, new)


def test_a_file_without_a_version_line_is_refused():
    with pytest.raises(br.Refused):
        br.version_only("nothing here\n", "nothing here\n")


def test_the_version_check_cli_compares_two_files(tmp_path):
    old, new, bad = tmp_path / "old", tmp_path / "new", tmp_path / "bad"
    old.write_text(PYPROJECT.format(v="1.0.0"))
    new.write_text(PYPROJECT.format(v="1.1.0"))
    bad.write_text(PYPROJECT.format(v="1.1.0") + "import os\n")
    assert br.main(["version-only", str(old), str(new)]) == 0
    assert br.main(["version-only", str(old), str(bad)]) == 1


def test_the_release_files_no_longer_include_files_a_release_cut_never_commits():
    assert "src-tauri/tauri.conf.json" not in br.RELEASE_FILES  # it holds the updater endpoints
    assert "src-tauri/Cargo.toml" not in br.RELEASE_FILES
    with pytest.raises(br.Refused):
        br.release_files_only(["CHANGELOG.md", "src-tauri/tauri.conf.json"])
    assert set(br.VERSION_ONLY_FILES) <= set(br.RELEASE_FILES)


# --- the beta must really have been built by Cut Beta -----------------------------------------------


def _assets(*names):
    return [{"name": n} for n in names]


def test_a_beta_with_its_manifest_and_signed_bundle_is_accepted():
    assert br.beta_built(
        _assets(
            "latest.json", "Anthill.Beta_aarch64.app.tar.gz", "Anthill.Beta_aarch64.app.tar.gz.sig"
        )
    )


@pytest.mark.parametrize(
    "names",
    [
        [],
        ["latest.json"],
        ["Anthill.Beta_aarch64.app.tar.gz", "Anthill.Beta_aarch64.app.tar.gz.sig"],
        ["latest.json", "Anthill.Beta_aarch64.app.tar.gz"],  # unsigned
    ],
)
def test_a_hand_made_or_half_built_beta_is_refused(names):
    with pytest.raises(br.Refused):
        br.beta_built(_assets(*names))


# --- the workflow ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load((ROOT / ".github/workflows/desktop-promote.yml").read_text())


@pytest.fixture(scope="module")
def wf_text():
    return (ROOT / ".github/workflows/desktop-promote.yml").read_text()


def _on(wf):
    return wf.get("on", wf.get(True))


def test_promote_is_hand_started_only_and_defaults_to_a_dry_run(wf):
    on = _on(wf)
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert inputs["beta"]["required"] is True
    assert wf["permissions"] == {"contents": "read"}  # the checks can read, never write


def test_every_check_runs_in_the_verify_job_before_anything_is_created(wf, wf_text):
    verify = wf["jobs"]["verify"]
    text = "\n".join(str(s.get("run", "")) for s in verify["steps"])
    for check in (
        "is-owner",
        "promotable",
        "release-files-only",
        "version-only",
        "beta-built",
        "check-release.sh",
        "stable-version",
        "ci-green",
        "tester-pass",
    ):
        assert check in text, check
    assert "refs/heads/main" in text
    assert (
        "commits/${BETA_SHA}/statuses" in text
    )  # a structured status, not text scraped from a comment
    assert "/comments" not in text
    assert "git/refs" not in text and "app-token" not in str(
        verify
    )  # verify creates nothing, mints no token


def test_only_the_second_job_can_create_the_tag_and_it_waits_for_approval(wf):
    promote = wf["jobs"]["promote"]
    assert promote["needs"] == "verify"
    assert promote["environment"] == "production"
    assert "inputs.dry_run == false" in promote["if"]
    steps = promote["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert any(u.startswith("actions/create-github-app-token") for u in uses)
    tag_step = next(s for s in steps if s.get("name") == "Create the stable tag")
    assert "git/refs" in tag_step["run"] and "refs/tags/v${VERSION}" in tag_step["run"]
    assert "apptoken.outputs.token" in str(tag_step["env"])


def test_the_tag_is_made_at_the_commit_that_was_checked_and_only_if_main_has_not_moved(wf):
    steps = wf["jobs"]["promote"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Main has not moved since the checks") < names.index("Create the stable tag")
    assert wf["jobs"]["promote"]["env"]["RELEASE_SHA"] == "${{ needs.verify.outputs.release_sha }}"
    assert (
        "refs/heads/main"
        in next(s for s in steps if s.get("name") == "Main has not moved since the checks")["run"]
    )


def test_promote_publishes_nothing_itself(wf_text):
    """Releasing is done by today's two stable workflows, started by the tag; Promote only makes the tag."""
    code = "\n".join(ln for ln in wf_text.splitlines() if not ln.lstrip().startswith("#"))
    for forbidden in ("gh release", "tauri-action", "action-gh-release", "upload"):
        assert forbidden not in code, forbidden


def test_the_stable_tag_it_creates_starts_the_stable_workflows():
    for name in ("release.yml", "desktop-release.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        assert re.search(r'tags: \["v\[0-9\]\+\.\[0-9\]\+\.\[0-9\]\+"\]', text), name


# --- the owner list is in a protected file ----------------------------------------------------------


def test_the_release_owner_list_lives_under_a_code_owner_protected_folder():
    """Whoever can edit the owner file decides who promotes, so it must sit under a CODEOWNERS entry (the
    merge ruleset enforces code owner review) and need no YAML parser or extra dependency to read."""
    assert (ROOT / ".github/release-owners.txt").is_file()
    assert re.search(r"(?m)^/\.github/\s+@\S+", (ROOT / ".github/CODEOWNERS").read_text())
    assert "pyyaml" not in (ROOT / ".github/workflows/desktop-promote.yml").read_text().lower()
