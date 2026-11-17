#!/usr/bin/env python3
"""Diff-scoped lint gate: hold NEW code to a stricter bar without touching the legacy tree.

Ruff reports a rule's findings for whole files, so turning a strict rule on globally would flag every line
that was already there. This keeps only the findings that sit on a line the change ADDED, so the rules below
apply to new code from today and the existing modules are never judged:

  BLE001   a blind `except Exception` (annotate a deliberate one with `# noqa: BLE001 - why`)
  ERA001   commented-out code
  C901 / PLR0912 / PLR0915   a function too complex, with too many branches, or too long

C901 and the PLR rules report at the `def` line, so a change inside an old, complex function is not flagged;
a new complex function is. Only the added lines of the change decide.

Usage: diff-lint.py <ruff.json> <diff-file> [--warn CODE[,CODE...]]
  ruff.json: `ruff check --output-format json` output. diff-file: `git diff -U0` (or any unified diff).
  --warn: codes reported as warnings that do not fail the run (an advisory rule). Everything else fails it.
Exit 0 = nothing blocking on added lines, 1 = at least one blocking finding (printed as GitHub annotations),
2 = unreadable input. The inputs are data.
"""

import json
import os
import re
import sys

HINTS = {
    "BLE001": "catch the specific exception, or if degrading gracefully is the point, add "
    "`# noqa: BLE001 - <why>` on that line",
    "ERA001": "delete commented-out code (git keeps the history)",
    "C901": "split this function into smaller ones",
    "PLR0912": "split this function: it has too many branches",
    "PLR0915": "split this function: it has too many statements",
}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(diff_text):
    """{path: set(new-file line numbers the change added)} from a unified diff."""
    added: dict[str, set[int]] = {}
    path, line = None, 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].split("\t")[0]
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            continue
        if raw.startswith(
            ("--- ", "diff ", "index ", "new file", "deleted file", "similarity", "rename")
        ):
            continue
        m = HUNK.match(raw)
        if m:
            line = int(m.group(1))
            continue
        if path is None:
            continue
        if raw.startswith("+"):
            added.setdefault(path, set()).add(line)
            line += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            line += 1
    return added


def findings_on_added_lines(ruff_findings, added, root=None):
    root = root or os.getcwd()
    hits = []
    for f in ruff_findings:
        rel = os.path.relpath(f.get("filename", ""), root).replace(os.sep, "/")
        row = (f.get("location") or {}).get("row")
        if row in added.get(rel, ()):
            hits.append(
                (
                    rel,
                    row,
                    (f.get("location") or {}).get("column", 1),
                    f.get("code"),
                    f.get("message", ""),
                )
            )
    return sorted(hits)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)[1:]
    warn: set[str] = set()
    if "--warn" in argv:
        i = argv.index("--warn")
        warn = {
            c.strip().upper()
            for c in (argv[i + 1] if i + 1 < len(argv) else "").split(",")
            if c.strip()
        }
        del argv[i : i + 2]
    if len(argv) != 2:
        sys.stderr.write("usage: diff-lint.py <ruff.json> <diff-file> [--warn CODE[,CODE...]]\n")
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            ruff = json.load(fh)
        with open(argv[1], encoding="utf-8", errors="replace") as fh:
            diff = fh.read()
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"diff-lint: cannot read an input: {exc}\n")
        return 2
    if not isinstance(ruff, list):
        sys.stderr.write("diff-lint: the ruff file is not a list of findings\n")
        return 2
    hits = findings_on_added_lines(ruff, added_lines(diff))
    blocking = 0
    for path, row, col, code, message in hits:
        hint = HINTS.get(code, "")
        level = "warning" if code in warn else "error"
        blocking += level == "error"
        print(
            f"::{level} file={path},line={row},col={col}::{code} {message}"
            + (f" - {hint}" if hint else "")
        )
    if hits:
        print(
            f"{len(hits)} finding(s) on lines this change added ({blocking} blocking, {len(hits) - blocking} "
            "advisory). These rules apply to new code only."
        )
    else:
        print("diff-lint: no findings on added lines.")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
