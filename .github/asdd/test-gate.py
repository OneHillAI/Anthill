#!/usr/bin/env python3
"""Pre-merge test-presence gate: a change to Python product code must change a test.

AGENTS.md makes "tests for new behavior" part of every PR, and the review lens was the only thing asking for
it. This is the deterministic floor: a PR that changes `anthill/**/*.py` must also add or change something
under `tests/`. It does not judge whether the test is good (the review does), only that there is one.

Exempt, like the changelog gate: the chore lane, a machine author, and a release cut (a version bump touches
anthill/__init__.py and needs no test). A change that touches no Python product code (docs, CI, a static
asset) is not covered. The release-cut rule is loaded from operate/docsync-render.py so it is defined once.

Usage: test-gate.py --changed FILE --labels FILE [--title TITLE] [--machine true|false]
  FILE: one path (or label) per line. Exit 0 = nothing missing, 1 = a test change is required and absent.
Untrusted text (the title) is only matched as data, never executed.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCT_PY = re.compile(r"^anthill/.+\.py$")


def load_release_cut():
    spec = importlib.util.spec_from_file_location(
        "docsync_render", HERE / "operate" / "docsync-render.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.is_release_cut


def lines(path):
    try:
        return [
            ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
    except OSError:
        return []


def verdict(changed, labels, title="", machine=False, is_release_cut=None):
    """(required, has_test_change, reason)."""
    has_test = any(p.startswith("tests/") for p in changed)
    if not any(PRODUCT_PY.match(p) for p in changed):
        return False, has_test, "no Python product code changed"
    if machine:
        return False, has_test, "a machine author's change"
    if "chore" in labels:
        return False, has_test, "a chore (exempt lane)"
    if (is_release_cut or load_release_cut())(changed, title):
        return False, has_test, "a release cut"
    return True, has_test, "a change to Python product code (anthill/)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--changed", required=True, help="file of changed paths, one per line")
    ap.add_argument("--labels", required=True, help="file of PR labels, one per line")
    ap.add_argument("--title", default="")
    ap.add_argument("--machine", default="false")
    a = ap.parse_args(argv)
    required, has_test, why = verdict(
        lines(a.changed), set(lines(a.labels)), a.title, a.machine.strip().lower() == "true"
    )
    if required and not has_test:
        print(
            f"::error file=AGENTS.md::{why[0].upper() + why[1:]} must add or change a test under tests/ "
            "(AGENTS.md: tests for new behavior). Prefer a model-free unit test in the style of the existing "
            "ones. If no behaviour changed (a rename, a typing-only fix), use the chore lane."
        )
        return 1
    print(f"Tests: {'test change present' if has_test else 'none needed'} ({why}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
