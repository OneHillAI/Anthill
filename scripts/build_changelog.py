#!/usr/bin/env python3
"""Assemble changelog.d/ fragments into a CHANGELOG.md release section (Towncrier-style).

Why: every PR used to edit the shared ``## [Unreleased]`` block, so each merge mechanically
re-conflicted every other open PR's changelog lines. With fragments, each PR instead adds ONE file
``changelog.d/<id>.<category>.md`` holding just its bullet body, so two PRs never touch the same file
and changelog conflicts become structurally impossible. See docs/specs/changelog-fragments.md.

At release time this concatenates the fragments into a ``## [X.Y.Z] - DATE`` section under the
Unreleased header, grouped by Keep a Changelog category order, and (unless --draft) deletes the
consumed fragments so the directory returns to empty.

Usage:
  scripts/build_changelog.py <version> <YYYY-MM-DD>   cut the section into CHANGELOG.md + clear fragments
  scripts/build_changelog.py --draft                  print the pending section, change nothing
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAG_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"

# Keep a Changelog heading order (matches the headings we already use).
ORDER = ["added", "changed", "deprecated", "removed", "fixed", "security"]
_NAME_RE = re.compile(rf"^(?P<id>.+?)\.(?P<cat>{'|'.join(ORDER)})\.md$", re.I)


def _fragments() -> list[tuple[str, tuple, str, Path]]:
    """(category, sort_key, body, path) for every valid fragment. Ignores .gitkeep / README.md and any
    file whose name isn't ``<id>.<category>.md``."""
    out = []
    for p in sorted(FRAG_DIR.glob("*.md")):
        m = _NAME_RE.match(p.name)
        if not m:
            continue
        ident = m.group("id")
        # numeric ids sort numerically (then lexically) so output is deterministic across machines
        key = (0, int(ident), "") if ident.isdigit() else (1, 0, ident)
        body = p.read_text().strip()
        if body:
            out.append((m.group("cat").lower(), key, body, p))
    return out


def render_section(version: str, date: str) -> tuple[str, list[Path]]:
    """The ``## [version] - date`` section text + the fragment paths it consumes."""
    frags = _fragments()
    lines = [f"## [{version}] - {date}", ""]
    for cat in ORDER:
        items = sorted((f for f in frags if f[0] == cat), key=lambda f: f[1])
        if not items:
            continue
        lines += [f"### {cat.capitalize()}", ""]
        for _cat, _key, body, _p in items:
            body_lines = body.splitlines()
            lines.append("- " + body_lines[0])
            # keep any continuation lines of a multi-line bullet indented under it
            lines += ["  " + line if line.strip() else "" for line in body_lines[1:]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", [f[3] for f in frags]


def cut(version: str, date: str) -> None:
    section, consumed = render_section(version, date)
    if not consumed:
        sys.exit(f"error: no fragments in {FRAG_DIR}/ to assemble for {version}")
    text = CHANGELOG.read_text()
    marker = "## [Unreleased]"
    start = text.find(marker)
    if start < 0:
        sys.exit("error: CHANGELOG.md has no '## [Unreleased]' header")
    # insert the new section between the Unreleased block and the next release section
    nxt = text.find("\n## [", start + len(marker))
    head, tail = (text[:nxt], text[nxt:]) if nxt >= 0 else (text, "")
    CHANGELOG.write_text(head.rstrip() + "\n\n" + section + tail)
    for p in consumed:
        p.unlink()
    print(f"assembled {len(consumed)} fragment(s) into ## [{version}] and cleared {FRAG_DIR}/")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    if argv[0] == "--draft":
        section, consumed = render_section("Unreleased", "draft")
        print(section if consumed else "(no pending changelog fragments)")
        return
    version = argv[0].lstrip("v")
    if len(argv) < 2 or not argv[1].strip():
        sys.exit(
            "usage: build_changelog.py <version> <YYYY-MM-DD>  (pass the release date explicitly)"
        )
    cut(version, argv[1].strip())


if __name__ == "__main__":
    main(sys.argv[1:])
