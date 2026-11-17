"""The Agent: trailer rule at intake (spec: docs/specs/175-intake-trailer-and-changelog-gate.md).

AGENTS.md and `.asdd.yml` say every agent-authored commit carries `Agent: <name> (automated, instructed-by:
<human>)`, but intake only checked DCO. Two rules now, read against the BASE commit's `agents` list:
(a) a commit whose author name or email contains a listed agent identity must carry the trailer;
(b) a PR that ticks the AI-agent box has the trailer on at least one commit.
A human commit in a mixed PR is not asked for one, and without the author files or an agents list the rule is
inert, so older callers are unchanged. These drive intake-check.sh and config-list.sh over synthetic workdirs.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = str(ROOT / ".github" / "asdd" / "intake-check.sh")
LISTER = str(ROOT / ".github" / "asdd" / "config-list.sh")
BOT = "onehill-dev-agent[bot]"
BOT_EMAIL = "310104697+onehill-dev-agent[bot]@users.noreply.github.com"
TRAILER = "Agent: Claude Sonnet 5.5 (Claude Code) (automated, instructed-by: awchristoph)"
AI_BODY = "- [x] Authored or co-authored by an **AI agent under human direction**.\n"
HUMAN_BODY = "- [x] Authored by a **human**.\n"


def _msg(subject, *, trailer=True, trailer_text=TRAILER):
    body = f"{subject}\n\n"
    if trailer:
        body += trailer_text + "\n"
    return body + "Signed-off-by: Someone <someone@example.com>\n"


def _workdir(
    tmp_path,
    commits,
    *,
    body=AI_BODY,
    authors=None,
    emails=None,
    agents=(BOT,),
    base_config=True,
    labels="chore\n",
):
    (tmp_path / "body.md").write_text(body)
    (tmp_path / "commits.txt").write_bytes(b"".join(c.encode() + b"\x00" for c in commits))
    (tmp_path / "labels.txt").write_text(labels)
    (tmp_path / "changed.txt").write_text("M\ttests/x.py\n")
    (tmp_path / "meta.env").write_text("pr_number=9\nhead_sha=deadbeef\n")
    if authors is not None:
        (tmp_path / "authors.txt").write_text("".join(a + "\n" for a in authors))
    if emails is not None:
        (tmp_path / "author-emails.txt").write_text("".join(e + "\n" for e in emails))
    if base_config:
        (tmp_path / "base-asdd.yml").write_text(
            "agents:\n" + "".join(f'  - "{a}"\n' for a in agents) + "\nlane_labels:\n  - chore\n"
        )
    return tmp_path


def _intake(workdir):
    out = workdir / "intake.json"
    subprocess.run(
        ["/bin/bash", GATE, str(workdir), str(out)], capture_output=True, text=True, cwd=str(ROOT)
    )
    return json.loads(out.read_text())


def _bot_commits(tmp_path, commits, **kw):
    n = len(commits)
    return _workdir(tmp_path, commits, authors=[BOT] * n, emails=[BOT_EMAIL] * n, **kw)


def test_an_agent_commit_with_the_trailer_passes(tmp_path):
    data = _intake(_bot_commits(tmp_path, [_msg("fix: a")]))
    assert data["agent_trailer_ok"] is True and data["passed"] is True


def test_an_agent_commit_without_the_trailer_fails(tmp_path):
    data = _intake(_bot_commits(tmp_path, [_msg("fix: a", trailer=False)], body=HUMAN_BODY))
    assert data["agent_trailer_ok"] is False and data["passed"] is False
    assert any("Agent trailer: 1 of 1" in p for p in data["problems"])


def test_a_non_canonical_trailer_does_not_count(tmp_path):
    bad = _msg("fix: a", trailer_text="Agent: Claude")
    data = _intake(_bot_commits(tmp_path, [bad], body=HUMAN_BODY))
    assert data["agent_trailer_ok"] is False


def test_a_human_commit_in_a_mixed_pr_is_not_asked_for_a_trailer(tmp_path):
    commits = [_msg("fix: a"), _msg("fix: b", trailer=False)]
    wd = _workdir(
        tmp_path,
        commits,
        authors=[BOT, "Ada Lovelace"],
        emails=[BOT_EMAIL, "ada@example.com"],
    )
    data = _intake(wd)
    assert data["agent_trailer_ok"] is True and data["passed"] is True


def test_an_unmarked_agent_commit_among_marked_ones_is_still_caught(tmp_path):
    commits = [_msg("fix: a"), _msg("fix: b", trailer=False)]
    data = _intake(_bot_commits(tmp_path, commits))
    assert data["agent_trailer_ok"] is False
    assert any("1 of 2" in p for p in data["problems"])


def test_a_human_pr_with_no_trailer_is_unchanged(tmp_path):
    wd = _workdir(
        tmp_path,
        [_msg("fix: a", trailer=False)],
        body=HUMAN_BODY,
        authors=["Ada Lovelace"],
        emails=["ada@example.com"],
    )
    data = _intake(wd)
    assert data["agent_trailer_ok"] is True and data["passed"] is True


def test_a_ticked_ai_box_needs_the_trailer_on_at_least_one_commit(tmp_path):
    no_ids = _workdir(tmp_path, [_msg("fix: a", trailer=False)])  # no author files at all
    data = _intake(no_ids)
    assert data["agent_trailer_ok"] is False and data["passed"] is False
    assert any("AI-agent box is ticked" in p for p in data["problems"])
    sub = tmp_path / "ok"
    sub.mkdir()
    ok = _intake(_workdir(sub, [_msg("fix: a", trailer=False), _msg("fix: b")]))
    assert ok["agent_trailer_ok"] is True


def test_the_human_box_alone_does_not_trigger_the_box_rule(tmp_path):
    data = _intake(_workdir(tmp_path, [_msg("fix: a", trailer=False)], body=HUMAN_BODY))
    assert data["agent_trailer_ok"] is True


def test_identity_matches_on_name_or_email_and_ignores_case(tmp_path):
    by_email = _workdir(
        tmp_path,
        [_msg("fix: a", trailer=False)],
        body=HUMAN_BODY,
        authors=["Someone Else"],
        emails=[BOT_EMAIL.upper()],
    )
    assert _intake(by_email)["agent_trailer_ok"] is False
    sub = tmp_path / "name"
    sub.mkdir()
    by_name = _workdir(
        sub,
        [_msg("fix: a", trailer=False)],
        body=HUMAN_BODY,
        authors=["ONEHILL-DEV-AGENT[BOT]"],
        emails=["x@example.com"],
    )
    assert _intake(by_name)["agent_trailer_ok"] is False


def test_the_rule_is_inert_without_author_files_or_an_agents_list(tmp_path):
    base = {"body": HUMAN_BODY}
    no_files = _workdir(tmp_path, [_msg("fix: a", trailer=False)], **base)
    assert _intake(no_files)["agent_trailer_ok"] is True  # older caller: no author data
    sub = tmp_path / "nolist"
    sub.mkdir()
    no_list = _bot_commits(sub, [_msg("fix: a", trailer=False)], agents=(), **base)
    assert _intake(no_list)["agent_trailer_ok"] is True  # an empty agents list names no one
    sub2 = tmp_path / "noconfig"
    sub2.mkdir()
    no_cfg = _bot_commits(sub2, [_msg("fix: a", trailer=False)], base_config=False, **base)
    assert _intake(no_cfg)["agent_trailer_ok"] is True


def test_a_blank_message_does_not_shift_which_author_goes_with_which_commit(tmp_path):
    commits = ["", _msg("fix: a", trailer=False)]
    wd = _workdir(
        tmp_path,
        commits,
        body=HUMAN_BODY,
        authors=["Ada Lovelace", BOT],
        emails=["ada@example.com", BOT_EMAIL],
    )
    assert _intake(wd)["agent_trailer_ok"] is False


def test_the_owner_override_still_overrides_the_trailer_rule(tmp_path):
    wd = _bot_commits(tmp_path, [_msg("fix: a", trailer=False)], body=HUMAN_BODY)
    (wd / "meta.env").write_text("pr_number=9\nhead_sha=deadbeef\noverride=true\n")
    data = _intake(wd)
    assert data["agent_trailer_ok"] is False and data["passed"] is True


def test_a_machine_authors_pr_is_not_affected(tmp_path):
    wd = _workdir(
        tmp_path,
        [_msg("chore(deps): bump x", trailer=False)],
        body="Bumps x.\n",
        authors=["dependabot[bot]"],
        emails=["49699333+dependabot[bot]@users.noreply.github.com"],
        labels="dependencies\n",
    )
    (wd / "author.txt").write_text("dependabot[bot]\n")
    (wd / "meta.env").write_text("pr_number=9\nhead_sha=deadbeef\nmachine_author=true\n")
    data = _intake(wd)
    assert data["machine_author"] is True and data["agent_trailer_ok"] is True
    assert data["passed"] is True


# --- config-list.sh -------------------------------------------------------------------------------


def _list(config_text, key, tmp_path):
    cfg = tmp_path / "cfg.yml"
    cfg.write_text(config_text)
    r = subprocess.run(["/bin/bash", LISTER, key, str(cfg)], capture_output=True, text=True)
    return r.stdout.split()


def test_config_list_reads_one_list_and_stops_at_the_next_key(tmp_path):
    text = 'agents:\n  - "a[bot]"  # who\n  - b-c\nmachine_authors:\n  - "d[bot]"\n'
    assert _list(text, "agents", tmp_path) == ["a[bot]", "b-c"]
    assert _list(text, "machine_authors", tmp_path) == ["d[bot]"]
    assert _list(text, "missing", tmp_path) == []


def test_config_list_of_a_missing_file_is_empty(tmp_path):
    r = subprocess.run(
        ["/bin/bash", LISTER, "agents", str(tmp_path / "nope.yml")], capture_output=True, text=True
    )
    assert r.returncode == 0 and r.stdout == ""


def test_the_workflow_hands_the_gate_the_author_files_and_the_base_config():
    text = (ROOT / ".github/workflows/asdd-intake.yml").read_text()
    assert "--format='%ae' > .asdd-work/author-emails.txt" in text
    assert "--format='%an' > .asdd-work/authors.txt" in text
    assert 'git show "$BASE_SHA:.asdd.yml" > .asdd-work/base-asdd.yml' in text
