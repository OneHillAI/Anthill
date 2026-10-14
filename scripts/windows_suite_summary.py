#!/usr/bin/env python3
"""Turn a junit file from the Windows full suite into a short markdown summary for the job page.

    python scripts/windows_suite_summary.py windows-suite.xml >> "$GITHUB_STEP_SUMMARY"

It prints the counts, then the failing tests grouped by test file (so one broken area reads as one line), each with the
first line of its message. A missing, unreadable or malformed file is reported as such and still exits 0: the job that
runs this is advisory (.github/workflows/windows-full-suite.yml), and a summary must not hide the real failure behind
another one.

Test names and messages are text from the run, not markdown: they go inside a fenced block whose fence is longer than
any run of backticks in them, so nothing in a message can close the block, render as HTML or turn into a link.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET  # nosec B405 - the file is the output of our own pytest run, not input from outside
from collections import defaultdict
from pathlib import Path

_MAX_FILES = 40  # a broken environment can fail hundreds of files; the list stays readable
_MAX_PER_FILE = 5
_MAX_NAME = (
    200  # a parametrised id can be thousands of characters; the job summary is limited to 1 MiB
)


def _int(value: str | None) -> int:
    """A junit count attribute as an int; anything missing or malformed counts as 0 rather than raising."""
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fenced(body: list[str]) -> list[str]:
    """``body`` as a fenced block that its own content cannot close (fence longer than its longest backtick run)."""
    longest = max((len(run) for run in re.findall(r"`+", "\n".join(body))), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + "text", *body, fence]


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _test_file(classname: str) -> str:
    """The test file behind a junit classname: ``tests.test_a`` and ``tests.test_a.TestX`` are both ``tests/test_a.py``."""
    parts = classname.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return "/".join(parts[: i + 1]) + ".py"
    return "/".join(parts) + ".py"


def summarise(path: Path) -> str:
    try:
        root = ET.parse(path).getroot()  # nosec B314 - see the import
    except (
        Exception
    ) as exc:  # any unreadable result must give the plain notice, never a failing step
        return f"### Windows full suite\n\nNo result file to read ({path}): {exc.__class__.__name__}. See the Test step's log.\n"

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(_int(s.get("tests")) for s in suites)
    failures = sum(_int(s.get("failures")) for s in suites)
    errors = sum(_int(s.get("errors")) for s in suites)
    skipped = sum(_int(s.get("skipped")) for s in suites)
    passed = max(0, total - failures - errors - skipped)

    broken: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for case in root.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        file_name = _test_file(case.get("classname") or "?")
        broken[file_name].append(
            (
                _one_line(case.get("name") or "?")[:_MAX_NAME],
                _first_line(problem.get("message") or problem.text),
            )
        )

    lines = [
        "### Windows full suite",
        "",
        f"{passed} passed, {failures + errors} failed, {skipped} skipped, of {total}.",
    ]
    if not broken:
        lines += [
            "",
            "Nothing failed on Windows. Remove `continue-on-error` from the Test step to make it a gate.",
        ]
        return "\n".join(lines) + "\n"

    body: list[str] = []
    for file_name in sorted(broken, key=lambda f: (-len(broken[f]), f))[:_MAX_FILES]:
        cases = broken[file_name]
        body.append(f"{_one_line(file_name)[:_MAX_NAME]}: {len(cases)} failed")
        for name, message in cases[:_MAX_PER_FILE]:
            body.append(f"  {name}" + (f": {message}" if message else ""))
        if len(cases) > _MAX_PER_FILE:
            body.append(f"  ... and {len(cases) - _MAX_PER_FILE} more")
    if len(broken) > _MAX_FILES:
        body.append(f"... and {len(broken) - _MAX_FILES} more files")
    lines += ["", f"{len(broken)} test file(s) with failures:", "", *_fenced(body)]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: windows_suite_summary.py <junit.xml>", file=sys.stderr)
        return 2
    sys.stdout.write(summarise(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
