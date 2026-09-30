#!/usr/bin/env python3
"""Consolidate merged PRs into grouped release notes that honor their contributors.

Deterministic and offline (spec R3): every fact comes from `git log` over a range and the commit
trailers the ASDD pipeline already requires on every PR - the Conventional Commit type, the PR number,
and the `Agent:` / `Co-Authored-By` / `Signed-off-by` trailers. No network, no model; run twice on a
range and get the same bytes. An optional agent lens (`.github/asdd/agents/release-notes.md`) curates a
highlights summary on top of this extract, and a human approves - this script never publishes or edits
CHANGELOG.md (spec R4).

The ASDD difference from the projects we studied: agents are disclosed first-class here, so the
Contributors section honors the human director AND the agent that did the work, never hiding either
(spec R2).

Usage:
  scripts/release_notes.py --since v0.10.0            # v0.10.0..HEAD
  scripts/release_notes.py --range v0.9.0..v0.10.0
  scripts/release_notes.py --since v0.10.0 --version 0.11.0 --json   # machine-readable extract

Output is a `## [x.y.z]` Markdown block (Keep a Changelog sections + a Contributors section) that
satisfies scripts/check-release.sh, ready to paste into CHANGELOG.md and to seed the release body.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field

# Conventional Commit subject: `type(scope)!: summary`. The summary usually ends with `(#123)`.
_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<summary>.+)$")
_PR = re.compile(r"\(#(?P<pr>\d+)\)")
_AGENT = re.compile(
    # The name may carry one extra parenthetical of its own (e.g. "Claude Sonnet 5 (Claude Code)")
    # before the required "(automated, instructed-by: ...)" suffix - found live cutting v0.12.7: a
    # sibling session's commit used exactly this shape and _credits below silently dropped its
    # credit (no human, no agent name) because the old pattern required "(automated," to follow the
    # name immediately, with nothing else in between.
    r"^Agent:\s*(?P<name>[^(]+?(?:\s*\([^)]*\))?)\s*\(automated,\s*instructed-by:\s*(?P<human>[^)]+?)\)\s*$",
    re.MULTILINE,
)
# A second real `Agent:` convention found in actual repo history (e.g. PR #850's own commit:
# "Agent: Claude Sonnet 5 (Claude Code) - Instructed by (human handle): awchristoph") - same meaning
# as _AGENT (agent name, then who instructed it), different punctuation. Without this, _credits below
# silently finds no agent/human trailer at all on every commit using this form, so the Contributors
# section quietly renders empty for an entire release despite every commit disclosing its agent - this
# was live-caught while cutting v0.12.0. Accepting both forms here (rather than asking every commit in
# history to be rewritten) is the only fix that doesn't lose real past credit.
_AGENT_ALT = re.compile(
    r"^Agent:\s*(?P<name>.+?)\s*-\s*Instructed by \(human handle\):\s*(?P<human>.+?)\s*$",
    re.MULTILINE,
)
_TRAILER = re.compile(
    r"^(?P<key>Co-Authored-By|Signed-off-by):\s*(?P<name>[^<]+?)\s*<(?P<email>[^>]+)>\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Conventional type -> Keep a Changelog section. Unmapped types fall to "Changed" so nothing is dropped.
_SECTION = {"feat": "Added", "fix": "Fixed"}
# Canonical Keep a Changelog order; only non-empty sections render.
_SECTION_ORDER = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]

# A Co-Authored-By / author that is actually a tool, not a person, is credited as an agent not a human.
_AI_HINT = re.compile(r"claude|gpt|copilot|codex|anthropic|openai|\bllm\b", re.IGNORECASE)
# Shared bot / CI identities that are neither a person nor a named agent - never credited as a human.
_BOT_HINT = re.compile(
    r"github-actions|dependabot|\[bot\]|noreply@|colonies ai|onehill ci|dev@onehill\.org",
    re.IGNORECASE,
)


@dataclass
class Commit:
    subject: str
    body: str
    author_name: str
    author_email: str


@dataclass
class Entry:
    pr: int | None
    type: str
    section: str
    title: str  # summary with the trailing "(#NN)" stripped
    breaking: bool
    humans: list[str] = field(default_factory=list)  # distinct human handles/names
    agents: list[tuple[str, str]] = field(default_factory=list)  # (agent_name, instructed_by)


def _clean_human(name: str) -> str:
    return name.strip().lstrip("@").strip()


def _credits(commit: Commit) -> tuple[list[str], list[tuple[str, str]]]:
    """(humans, agents) for one commit. Humans come from the Agent trailer's instructed-by and human
    co-authors; agents from the Agent trailer and any AI co-author. A shared bot/CI sign-off is never a
    human. Nothing is invented - only trailers (and, as a last resort, a human-looking author) count."""
    humans: list[str] = []
    agents: list[tuple[str, str]] = []
    directors: list[str] = []

    for pattern in (_AGENT, _AGENT_ALT):
        for m in pattern.finditer(commit.body):
            agent = m.group("name").strip()
            human = _clean_human(m.group("human"))
            if (agent, human) in agents:
                continue  # a commit body only ever carries one Agent: trailer - never double-count
            agents.append((agent, human))
            directors.append(human)
            if human and human not in humans:
                humans.append(human)

    has_agent_trailer = bool(agents)  # the `Agent:` trailer is the canonical agent identity
    for m in _TRAILER.finditer(commit.body):
        if m.group("key").lower() != "co-authored-by":
            continue
        name, email = m.group("name").strip(), m.group("email").strip()
        if _AI_HINT.search(name) or _AI_HINT.search(email):
            # An AI co-author of a commit that already carries an `Agent:` trailer is the SAME agent
            # under its git name - don't double-credit it. Only count it when no trailer named an agent.
            if not has_agent_trailer:
                director = directors[0] if directors else ""
                if (name, director) not in agents:
                    agents.append((name, director))
        elif not _BOT_HINT.search(name) and not _BOT_HINT.search(email):
            h = _clean_human(name)
            if h and h not in humans:
                humans.append(h)

    # Last resort: a human-looking commit author, when no trailer named anyone.
    if (
        not humans
        and not _BOT_HINT.search(commit.author_name)
        and not _BOT_HINT.search(commit.author_email)
        and not _AI_HINT.search(commit.author_name)
    ):
        humans.append(_clean_human(commit.author_name))

    return humans, agents


def entry_from_commit(commit: Commit) -> Entry | None:
    """Parse a commit into a release entry, or None if the subject is not a Conventional Commit."""
    m = _SUBJECT.match(commit.subject.strip())
    if not m:
        return None
    ctype = m.group("type")
    summary = m.group("summary").strip()
    pr_m = _PR.search(summary)
    pr = int(pr_m.group("pr")) if pr_m else None
    title = _PR.sub("", summary).strip().rstrip(" -").strip()
    breaking = bool(m.group("bang")) or "BREAKING CHANGE" in commit.body
    humans, agents = _credits(commit)
    return Entry(
        pr=pr,
        type=ctype,
        section=_SECTION.get(ctype, "Changed"),
        title=title,
        breaking=breaking,
        humans=humans,
        agents=agents,
    )


def entries_from_commits(commits: list[Commit]) -> list[Entry]:
    """Release entries for a list of commits, de-duplicated by PR number (first occurrence wins).
    Non-Conventional commits are skipped; commits with no PR each stand alone."""
    entries: list[Entry] = []
    seen_pr: set[int] = set()
    for c in commits:
        e = entry_from_commit(c)
        if e is None:
            continue
        if e.pr is not None:
            if e.pr in seen_pr:
                continue
            seen_pr.add(e.pr)
        entries.append(e)
    return entries


def _contributors(entries: list[Entry]) -> list[str]:
    """One honoring line per human, in first-seen order, with a change count and the agents they
    directed. Humans who never appear are never listed; agents are never hidden and never stand alone."""
    order: list[str] = []
    counts: dict[str, int] = {}
    directed: dict[str, list[str]] = {}
    for e in entries:
        credited = list(e.humans)
        # An agent's director is a human contributor even if no other trailer named them.
        for _agent, human in e.agents:
            if human and human not in credited:
                credited.append(human)
        for h in credited:
            if h not in counts:
                order.append(h)
                counts[h] = 0
                directed[h] = []
            counts[h] += 1
        for agent, human in e.agents:
            if human and agent not in directed[human]:
                directed[human].append(agent)

    lines: list[str] = []
    for h in order:
        n = counts[h]
        suffix = f" ({n} {'change' if n == 1 else 'changes'})"
        if directed[h]:
            suffix += f", directing {', '.join(directed[h])}"
        lines.append(f"- **{h}**{suffix}")
    return lines


def render(entries: list[Entry], version: str, date: str) -> str:
    """Render the `## [x.y.z]` Markdown block: Keep a Changelog sections then a Contributors section.
    Shape matches scripts/check-release.sh (spec R5)."""
    by_section: dict[str, list[Entry]] = {}
    for e in entries:
        by_section.setdefault(e.section, []).append(e)

    out = [f"## [{version}] - {date}", ""]
    for section in _SECTION_ORDER:
        items = by_section.get(section)
        if not items:
            continue
        out.append(f"### {section}")
        out.append("")
        for e in items:
            ref = f" (#{e.pr})" if e.pr else ""
            prefix = "**Breaking:** " if e.breaking else ""
            out.append(f"- {prefix}{e.title}{ref}")
        out.append("")

    contributors = _contributors(entries)
    if contributors:
        out.append("### Contributors")
        out.append("")
        out.append(
            "Thanks to everyone who shipped this release - the humans directing the work and,"
        )
        out.append("disclosed alongside them, the agents that did it:")
        out.append("")
        out.extend(contributors)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── git plumbing (the only impure part) ───────────────────────────────────────

_REC = "\x1e"  # between commits
_FLD = "\x1f"  # between fields
_FMT = f"%H{_FLD}%an{_FLD}%ae{_FLD}%s{_FLD}%b{_REC}"


def commits_from_log(text: str) -> list[Commit]:
    """Parse `git log --format=_FMT` output into commits. Pure, so tests feed synthetic log text."""
    commits: list[Commit] = []
    for rec in text.split(_REC):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split(_FLD)
        if len(parts) < 5:
            continue
        _hash, an, ae, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        commits.append(Commit(subject=subject, body=body, author_name=an, author_email=ae))
    return commits


def _git_log(rng: str) -> str:
    return subprocess.run(
        ["git", "log", rng, "--no-merges", f"--format={_FMT}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _latest_tag() -> str:
    return subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _today() -> str:
    # Release date; kept out of the pure core so the extractor stays reproducible.
    return subprocess.run(
        ["date", "-u", "+%Y-%m-%d"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since", help="start tag/ref; notes cover <since>..HEAD")
    g.add_argument("--range", help="explicit git range, e.g. v0.9.0..v0.10.0")
    ap.add_argument(
        "--version", help="version for the heading (default: derived from --since/--range)"
    )
    ap.add_argument("--date", help="release date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--json", action="store_true", help="emit the machine-readable extract instead")
    args = ap.parse_args(argv)

    if args.range:
        rng = args.range
    elif args.since:
        rng = f"{args.since}..HEAD"
    else:
        rng = f"{_latest_tag()}..HEAD"

    entries = entries_from_commits(commits_from_log(_git_log(rng)))
    version = args.version or "x.y.z"
    date = args.date or _today()

    if args.json:
        import json

        print(
            json.dumps(
                [
                    {
                        "pr": e.pr,
                        "type": e.type,
                        "section": e.section,
                        "title": e.title,
                        "breaking": e.breaking,
                        "humans": e.humans,
                        "agents": [{"agent": a, "instructed_by": h} for a, h in e.agents],
                    }
                    for e in entries
                ],
                indent=2,
            )
        )
    else:
        print(render(entries, version, date))
    return 0


if __name__ == "__main__":
    sys.exit(main())
