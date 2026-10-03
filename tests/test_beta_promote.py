"""Promote Beta: only an owner, only a beta tested as is, and only behind a second approval.

Pins, with no network and no release:

- who may promote (`release_owners` in .asdd.yml) and which beta is promotable;
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


def test_the_real_config_lists_a_release_owner():
    text = (ROOT / ".asdd.yml").read_text()
    assert br.is_owner("welsbach", text)
    assert br.is_owner("WelsBach", text)  # logins are case-insensitive
    with pytest.raises(br.Refused):
        br.is_owner("someone-else", text)


@pytest.mark.parametrize(
    "config", ["", "other: 1\n", "release_owners:\nnext: 1\n", "release_owners: []\n"]
)
def test_no_configured_owner_means_nobody_can_promote(config):
    with pytest.raises(br.Refused):
        br.is_owner("welsbach", config)


def test_owners_are_read_from_a_quoted_or_bare_list_and_stop_at_the_next_key():
    text = 'release_owners:\n  - "alice"\n  - bob  # a comment\n  - \'carol\'\nreview_override_owners:\n  - "dave"\n'
    for ok in ("alice", "bob", "carol"):
        assert br.is_owner(ok, text)
    with pytest.raises(br.Refused):
        br.is_owner("dave", text)  # an owner of a different list


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


# --- the test agent's PASS ---------------------------------------------------------------------------


def _report(verdict, *, sha=SHA, who=BOT, at="2026-10-03T10:00:00Z", cid=1):
    return {
        "id": cid,
        "created_at": at,
        "user": who,
        "body": f"## Test agent - result for `{sha}`\n\n**{verdict}** (agent-reported; run on `m`)\n",
    }


def test_a_pass_from_the_bot_for_that_commit_is_accepted():
    assert br.tester_passed([_report("PASS")], SHA)


@pytest.mark.parametrize("verdict", ["FAIL", "NO VERDICT (the agent wrote an unusable result)"])
def test_a_fail_or_no_verdict_is_refused(verdict):
    with pytest.raises(br.Refused):
        br.tester_passed([_report(verdict)], SHA)


def test_no_report_for_the_commit_is_refused():
    with pytest.raises(br.Refused):
        br.tester_passed([], SHA)
    with pytest.raises(br.Refused):
        br.tester_passed([_report("PASS", sha="b" * 40)], SHA)  # a report for a different commit
    with pytest.raises(br.Refused):
        br.tester_passed([{"body": "a human comment", "user": BOT}], SHA)


def test_a_human_cannot_fake_the_pass():
    forged = _report("PASS", who={"login": "welsbach"})
    with pytest.raises(br.Refused):
        br.tester_passed([forged], SHA)


def test_a_report_edited_after_posting_is_refused():
    """A PASS pasted over a FAIL (or any later edit) must not count; the agent posts new comments, never edits."""
    edited = {**_report("PASS"), "updated_at": "2026-10-03T12:00:00Z"}
    with pytest.raises(br.Refused):
        br.tester_passed([edited], SHA)
    untouched = {**_report("PASS"), "updated_at": "2026-10-03T10:00:00Z"}  # same as created_at
    assert br.tester_passed([untouched], SHA)


def test_the_newest_report_wins():
    older_fail_newer_pass = [
        _report("FAIL", at="2026-10-03T09:00:00Z", cid=1),
        _report("PASS", at="2026-10-03T11:00:00Z", cid=2),
    ]
    assert br.tester_passed(older_fail_newer_pass, SHA)
    older_pass_newer_fail = [
        _report("PASS", at="2026-10-03T09:00:00Z", cid=1),
        _report("FAIL", at="2026-10-03T11:00:00Z", cid=2),
    ]
    with pytest.raises(br.Refused):
        br.tester_passed(older_pass_newer_fail, SHA)


def test_the_cli_exit_codes(tmp_path):
    cfg = tmp_path / "asdd.yml"
    cfg.write_text('release_owners:\n  - "alice"\n')
    assert br.main(["is-owner", "--actor", "alice", "--config", str(cfg)]) == 0
    assert br.main(["is-owner", "--actor", "bob", "--config", str(cfg)]) == 1
    assert br.main(["promotable", "--beta", "v1.1.0-rc.1", "--tags", "v1.0.0\nv1.1.0-rc.1"]) == 0
    assert br.main(["promotable", "--beta", "v1.1.0-rc.1", "--tags", "v1.0.0"]) == 1


# --- only a version bump in the files that could carry behaviour ------------------------------------------


def _diff(*lines):
    return "\n".join(
        [
            "diff --git a/pyproject.toml b/pyproject.toml",
            "--- a/pyproject.toml",
            "+++ b/pyproject.toml",
            "@@ -7 +7 @@",
            *lines,
        ]
    )


def test_a_version_bump_alone_is_allowed():
    assert br.version_only(_diff('-version = "1.0.0"', '+version = "1.1.0"'))
    assert br.version_only(_diff('-__version__ = "1.0.0"', '+__version__ = "1.1.0"'))
    assert br.version_only("")  # no change at all


@pytest.mark.parametrize(
    "smuggled",
    [
        '+    "evil-package>=1",',
        '+dependencies = ["x"]',
        "+import os",
        '-    "markitdown[pdf]>=0.1.6,<0.2",',
        '+version_hack = "1.1.0"',
    ],
)
def test_anything_besides_the_version_line_is_refused(smuggled):
    with pytest.raises(br.Refused):
        br.version_only(_diff('-version = "1.0.0"', '+version = "1.1.0"', smuggled))


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
        "commits/${BETA_SHA}/comments" in text
    )  # the report may be a commit comment, not only a PR comment
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
