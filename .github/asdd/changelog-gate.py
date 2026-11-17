#!/usr/bin/env python3
"""Pre-merge changelog gate: a change to product code must add a changelog fragment.

The post-merge documentation agent already decides, with `needs_changelog()` in operate/docsync-render.py,
that a change to anthill/ wants a fragment (a release cut and test, docs and CI-only changes do not). That
only produced a suggestion after the merge. This runs the very same rule, loaded from that file so the two
cannot drift, before the merge, and fails when no valid fragment is in the change.

A fragment is required when:
- the PR carries the `pillar:feature` label (the existing rule, whatever files changed), or
- product code changed (needs_changelog) and the lane is not `chore` (the conventions' exempt lane) and the
  author is not a machine author (Dependabot touches no product code and cannot write a fragment).

Usage: changelog-gate.py --changed FILE --labels FILE [--title TITLE] [--machine true|false]
  FILE: one path (or label) per line. Exit 0 = nothing missing, 1 = a fragment is required and absent.
Untrusted text (the title) is only matched as data, never executed.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAGMENT_RE = re.compile(
    r"^changelog\.d/.+\.(added|changed|deprecated|removed|fixed|security)\.md$"
)


def load_rule():
    """needs_changelog from the documentation agent's renderer, the single definition of the rule."""
    spec = importlib.util.spec_from_file_location(
        "docsync_render", HERE / "operate" / "docsync-render.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.needs_changelog


def lines(path):
    try:
        return [
            ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
    except OSError:
        return []


def verdict(changed, labels, title="", machine=False, needs_changelog=None):
    """(required, has_fragment, reason)."""
    needs_changelog = needs_changelog or load_rule()
    has_fragment = any(FRAGMENT_RE.match(p) for p in changed)
    if "pillar:feature" in labels:
        return True, has_fragment, "a pillar:feature change"
    if machine:
        return False, has_fragment, "a machine author's change"
    if "chore" in labels:
        return False, has_fragment, "a chore (exempt lane)"
    if needs_changelog(changed, title):
        return True, has_fragment, "a change to product code (anthill/)"
    return False, has_fragment, "no product code changed"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--changed", required=True, help="file of changed paths, one per line")
    ap.add_argument("--labels", required=True, help="file of PR labels, one per line")
    ap.add_argument("--title", default="")
    ap.add_argument("--machine", default="false")
    a = ap.parse_args(argv)
    required, has_fragment, why = verdict(
        lines(a.changed), set(lines(a.labels)), a.title, a.machine.strip().lower() == "true"
    )
    if required and not has_fragment:
        print(
            f"::error file=changelog.d/README.md::{why[0].upper() + why[1:]} must add a changelog fragment, "
            "changelog.d/<id>.<category>.md (category: added, changed, deprecated, removed, fixed or security). "
            "Describe the user-visible change in one or two sentences; see changelog.d/README.md. If nothing "
            "user-visible changed, use the chore lane."
        )
        return 1
    print(f"Changelog: {'fragment present' if has_fragment else 'none needed'} ({why}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
