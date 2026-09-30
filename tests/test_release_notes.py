"""Deterministic release-notes extraction (spec: docs/specs/release-notes-and-credits.md).

Model-free: the extractor derives everything from git subjects/bodies and the commit trailers the ASDD
pipeline already requires, so these tests feed synthetic commits and pin the grouping and the
human+agent contributor honoring.
"""

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SCRIPT)
rn = importlib.util.module_from_spec(_spec)
sys.modules["release_notes"] = rn  # so @dataclass can resolve the module during exec
_spec.loader.exec_module(rn)


def _commit(subject, body="", author="Onehill", email="dev@onehill.org"):
    return rn.Commit(subject=subject, body=body, author_name=author, author_email=email)


_AGENT_BODY = (
    "Agent: claude-opus-4-8 (automated, instructed-by: onehill)\n"
    "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>\n"
    "Signed-off-by: Onehill <dev@onehill.org>\n"
)


def test_conventional_subject_parses_type_pr_and_title():
    e = rn.entry_from_commit(_commit("feat: add a project home page (#419)", _AGENT_BODY))
    assert e.type == "feat" and e.section == "Added"
    assert e.pr == 419 and e.title == "add a project home page"


def test_non_conventional_subject_is_skipped():
    assert rn.entry_from_commit(_commit("Merge branch 'main'")) is None


def test_type_maps_to_keep_a_changelog_section():
    assert rn.entry_from_commit(_commit("fix: x (#1)")).section == "Fixed"
    assert rn.entry_from_commit(_commit("feat: y (#2)")).section == "Added"
    assert (
        rn.entry_from_commit(_commit("chore: z (#3)")).section == "Changed"
    )  # unmapped -> Changed


def test_breaking_change_is_flagged():
    assert rn.entry_from_commit(_commit("feat!: drop v1 api (#9)")).breaking is True
    body = "BREAKING CHANGE: config moved\n"
    assert rn.entry_from_commit(_commit("feat: reshape config (#10)", body)).breaking is True


def test_agent_trailer_credits_human_director_and_agent():
    e = rn.entry_from_commit(_commit("fix: gate metrics (#449)", _AGENT_BODY))
    assert e.humans == ["onehill"]  # the directing human, from instructed-by
    assert e.agents == [("claude-opus-4-8", "onehill")]  # the agent, disclosed


def test_ai_coauthor_is_an_agent_and_bot_signoff_is_not_a_human():
    # The AI Co-Authored-By is credited as an agent; the shared "Onehill" sign-off/author is nobody.
    e = rn.entry_from_commit(_commit("fix: gate metrics (#449)", _AGENT_BODY))
    assert "Onehill" not in e.humans and "Claude Opus 4.8" not in e.humans


# A second real `Agent:` trailer convention found in actual repo history (differs from _AGENT_BODY's
# "(automated, instructed-by: ...)" only in punctuation) - live-caught while cutting v0.12.0, when the
# Contributors section rendered empty for a whole release despite every commit disclosing its agent.
_AGENT_BODY_ALT = (
    "Signed-off-by: onehill-dev-agent[bot] <310104697+onehill-dev-agent[bot]@users.noreply.github.com>\n"
    "Agent: Claude Sonnet 5 (Claude Code) - Instructed by (human handle): awchristoph\n"
    "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
)


def test_alternate_agent_trailer_format_is_also_credited():
    e = rn.entry_from_commit(_commit("fix: gate metrics (#449)", _AGENT_BODY_ALT))
    assert e.humans == ["awchristoph"]
    assert e.agents == [("Claude Sonnet 5 (Claude Code)", "awchristoph")]


# A third real `Agent:` trailer shape found in actual repo history - the original _AGENT_BODY's own
# "(Claude Code)"-style agent name PLUS its "(automated, instructed-by: ...)" ending, combined. The
# old _AGENT pattern required "(automated," to follow the name immediately, so the extra "(Claude
# Code)" parenthetical made it match nothing at all - live-caught cutting v0.12.7, when a sibling
# session's commit using exactly this shape rendered with no human and no agent in the Contributors
# section, despite disclosing both.
_AGENT_BODY_PARENTHESIZED_NAME = (
    "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
    "Agent: Claude Sonnet 5 (Claude Code) (automated, instructed-by: awchristoph)\n"
    "Signed-off-by: onehill-dev-agent[bot] <310104697+onehill-dev-agent[bot]@users.noreply.github.com>\n"
)


def test_agent_name_with_its_own_parenthetical_is_still_credited():
    e = rn.entry_from_commit(_commit("fix: gate metrics (#449)", _AGENT_BODY_PARENTHESIZED_NAME))
    assert e.humans == ["awchristoph"]
    assert e.agents == [("Claude Sonnet 5 (Claude Code)", "awchristoph")]


def test_human_pr_credits_the_author_when_no_trailers():
    e = rn.entry_from_commit(
        _commit(
            "feat: add export (#5)", "Signed-off-by: Alice <alice@x.io>\n", "Alice", "alice@x.io"
        )
    )
    assert e.humans == ["Alice"] and e.agents == []


def test_entries_dedupe_by_pr_number():
    commits = [
        _commit("fix: first commit of pr (#7)"),
        _commit("fix: second commit of pr (#7)"),
        _commit("feat: other (#8)"),
    ]
    prs = [e.pr for e in rn.entries_from_commits(commits)]
    assert prs == [7, 8]  # #7 appears once


def test_render_produces_a_release_gate_compatible_heading():
    entries = rn.entries_from_commits([_commit("feat: a (#1)", _AGENT_BODY)])
    md = rn.render(entries, "0.11.0", "2026-07-13")
    # scripts/check-release.sh greps for ^## \[0\.11\.0\]
    assert md.startswith("## [0.11.0] - 2026-07-13")
    assert "### Added" in md and "- a (#1)" in md


def test_render_contributors_honors_human_and_agent():
    entries = rn.entries_from_commits(
        [_commit("feat: a (#1)", _AGENT_BODY), _commit("fix: b (#2)", _AGENT_BODY)]
    )
    md = rn.render(entries, "0.11.0", "2026-07-13")
    assert "### Contributors" in md
    # the human director is credited with a count, and the agent is disclosed alongside - not hidden
    assert "**onehill** (2 changes), directing claude-opus-4-8" in md


def test_log_parsing_round_trips():
    rec = rn._FLD.join(["deadbeef", "Alice", "alice@x.io", "feat: a (#1)", "body"]) + rn._REC
    commits = rn.commits_from_log(rec)
    assert len(commits) == 1 and commits[0].subject == "feat: a (#1)"
    assert commits[0].author_name == "Alice"


def test_render_is_deterministic():
    commits = [_commit("feat: a (#1)", _AGENT_BODY), _commit("fix: b (#2)")]
    a = rn.render(rn.entries_from_commits(commits), "0.11.0", "2026-07-13")
    b = rn.render(rn.entries_from_commits(commits), "0.11.0", "2026-07-13")
    assert a == b
