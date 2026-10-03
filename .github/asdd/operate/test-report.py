#!/usr/bin/env python3
"""Render the test agent's structured result as the markdown report the workflow posts.

Usage: test-report.py <result.json> <change_ref> <model>      (the markdown report, on stdout)
       test-report.py --verdict <result.json>                 (pass, fail or none, for the commit status)

The result file is MODEL OUTPUT, so only known fields are read, each is length-capped, and nothing in it is
executed. A verdict other than pass or fail reads "NO VERDICT", never a pass. Called by test.sh.
"""

import json
import sys


def clip(value, limit=300):
    return " ".join(str(value).split())[:limit]


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def verdict(result):
    """pass, fail, or none (no usable verdict). The structured signal the workflow records as a commit status."""
    v = str(result.get("verdict", "")).strip().lower()
    return v if v in ("pass", "fail") else "none"


def render(result, ref, model):
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    verdict = str(result.get("verdict", "")).strip().lower()
    head = {"pass": "PASS", "fail": "FAIL"}.get(
        verdict, "NO VERDICT (the agent wrote an unusable result)"
    )
    lines = [
        f"## Test agent - result for `{ref}`",
        "",
        f"**{head}** (agent-reported; run on `{model}`, the test_runner role)",
        "",
    ]
    if payload.get("tested"):
        lines.append(f"- Tested: {clip(payload['tested'])}")
    if "passed" in payload or "failed" in payload:
        lines.append(
            f"- Passed: {clip(payload.get('passed', '?'), 20)} / Failed: {clip(payload.get('failed', '?'), 20)}"
        )
    cases = payload.get("failing") if isinstance(payload.get("failing"), list) else []
    if cases:
        lines.append("- Failing cases: " + ", ".join(f"`{clip(c, 120)}`" for c in cases[:20]))
    if result.get("reasoning"):
        lines.append(f"- Reasoning: {clip(result['reasoning'])}")
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) == 3 and argv[1] == "--verdict":
        sys.stdout.write(verdict(load(argv[2])) + "\n")
        return 0
    if len(argv) != 4:
        sys.stderr.write("usage: test-report.py <result.json> <change_ref> <model>\n")
        return 2
    path, ref, model = argv[1:4]
    sys.stdout.write(render(load(path), ref, model))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
