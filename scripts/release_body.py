#!/usr/bin/env python3
"""Put a version's changelog section on the GitHub release page, so the page says what changed.

The Release workflow writes the download instructions (a fixed text). Without this, the release page told a
reader how to install and nothing about what is new (v1.0.0 shipped that way). This takes that text and adds:

- the version's opening Highlights paragraph(s) straight after the title, so the first thing a reader sees is
  what is new in plain language;
- the rest of the version's section (the grouped entries and the Contributors block) under a
  "What's changed in this version" heading, placed before the "Build from source" part when there is one.

Usage:
  scripts/release_body.py --version 1.0.1 --changelog CHANGELOG.md --base notes-base.md --out notes.md

Exits non-zero when CHANGELOG.md has no section for the version, so a release can never go out with a page that
says nothing about it (check-release.sh already refuses such a tag earlier in the same job).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SOURCE_MARKER = "### Build from source"


class MissingSection(Exception):
    pass


def section(changelog: str, version: str) -> str:
    """The text of `## [version]` up to the next `## [` heading, without the heading line itself."""
    lines = changelog.splitlines()
    head = re.compile(rf"^## \[{re.escape(version)}\](\s|$)")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        raise MissingSection(f"CHANGELOG.md has no '## [{version}]' section")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip("\n")


def split(body: str) -> tuple[str, str]:
    """(highlights, details): the text before the first `### ` heading, and everything from it on."""
    lines = body.splitlines()
    first = next((i for i, ln in enumerate(lines) if ln.startswith("### ")), len(lines))
    return "\n".join(lines[:first]).strip("\n"), "\n".join(lines[first:]).strip("\n")


def compose(base: str, changelog: str, version: str) -> str:
    highlights, details = split(section(changelog, version))
    lines = base.rstrip("\n").splitlines()
    title, rest = (lines[0], lines[1:]) if lines else ("", [])
    rest_text = "\n".join(rest).strip("\n")
    block = f"## What's changed in this version\n\n{details}" if details else ""
    if block and SOURCE_MARKER in rest_text:
        before, after = rest_text.split(SOURCE_MARKER, 1)
        rest_text = before.rstrip("\n") + "\n\n" + block + "\n\n" + SOURCE_MARKER + after
    elif block:
        rest_text = rest_text + "\n\n" + block
    parts = [title] + ([highlights] if highlights else []) + ([rest_text] if rest_text else [])
    return "\n\n".join(parts) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--changelog", default="CHANGELOG.md")
    ap.add_argument("--base", required=True, help="the release text the workflow already writes")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        text = compose(
            Path(a.base).read_text(encoding="utf-8"),
            Path(a.changelog).read_text(encoding="utf-8"),
            a.version,
        )
    except MissingSection as e:
        sys.stderr.write(f"release_body: {e}\n")
        return 1
    Path(a.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
