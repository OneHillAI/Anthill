#!/usr/bin/env python3
"""Turn a pytest run into the structured result the test report and the `asdd/test` status read.

Usage: test-junit.py <junit.xml> <pytest_exit_code> <change_ref>      (the result as JSON, on stdout)

The verdict comes from pytest's exit code, with the JUnit XML supplying counts and failing case names:

- exit 0, at least one test ran, no failure or error in the XML  -> pass
- exit 1 (pytest's "tests failed")                                -> fail, whatever the XML says
- anything else (interrupted, internal error, usage error, no tests collected, timeout, a missing or
  unreadable XML on an exit 0, an exit 0 whose XML shows failures) -> none, which records `error`

Only a clean run is a pass; every doubt is "no verdict", never a pass. Called by test.sh.
"""

import json
import sys
import xml.etree.ElementTree as ET

TESTED = "pytest -n 2 --dist loadscope -q"


def clip(value, limit=200):
    return " ".join(str(value).split())[:limit]


def read_junit(path):
    """(tests, skipped, failing case names) from a JUnit XML file, or None when it is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = skipped = 0
    failing = []
    for suite in suites:
        for case in suite.findall("testcase"):
            tests += 1
            if case.find("skipped") is not None:
                skipped += 1
            if case.find("failure") is not None or case.find("error") is not None:
                failing.append(
                    clip(f"{case.get('classname', '')}::{case.get('name', '')}".strip(":"))
                )
    return tests, skipped, failing


def build(junit_path, exit_code, ref):
    junit = read_junit(junit_path)
    tests, skipped, failing = junit if junit else (0, 0, [])
    if exit_code == 1:
        verdict, note = "fail", "pytest exited 1: at least one test failed or errored"
    elif exit_code == 0 and junit is None:
        verdict, note = (
            "none",
            "pytest exited 0 but left no readable JUnit XML, so the counts cannot be confirmed",
        )
    elif exit_code == 0 and failing:
        verdict, note = "none", "pytest exited 0 but the JUnit XML lists failures; the two disagree"
    elif exit_code == 0 and tests == 0:
        verdict, note = "none", "pytest exited 0 but ran no tests"
    elif exit_code == 0:
        verdict, note = "pass", f"pytest exited 0: {tests - skipped} passed, {skipped} skipped"
    else:
        verdict, note = "none", f"pytest did not complete (exit code {exit_code})"
    return {
        "action": "tests.run",
        "verdict": verdict,
        "runner": "pytest",
        "action_taken": "recorded the pytest result for the merged commit",
        "reasoning": note,
        "payload": {
            "tested": f"{TESTED} on {clip(ref, 80)}",
            "passed": max(tests - skipped - len(failing), 0) if junit else "?",
            "failed": len(failing) if junit else "?",
            "failing": failing[:50],
        },
    }


def main(argv):
    if len(argv) != 4:
        sys.stderr.write("usage: test-junit.py <junit.xml> <pytest_exit_code> <change_ref>\n")
        return 2
    try:
        code = int(argv[2])
    except ValueError:
        sys.stderr.write("test-junit: the exit code must be an integer\n")
        return 2
    json.dump(build(argv[1], code, argv[3]), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
