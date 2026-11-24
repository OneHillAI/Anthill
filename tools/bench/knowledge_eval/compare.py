"""Compare two result files: the pass rate by kind and the median first word, per model, with a note when the sets differ.

python tools/bench/knowledge_eval/compare.py before.json after.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def compare(before: dict, after: dict) -> list[str]:
    lines: list[str] = []
    if before.get("set_hash") != after.get("set_hash"):
        lines.append("NOTE: the two runs used different sets, so the figures are not comparable.")
    if before.get("scoring_version") != after.get("scoring_version"):
        lines.append("NOTE: the scoring changed between the runs.")
    for tag in sorted(set(before.get("results", {})) & set(after.get("results", {}))):
        b, a = before["results"][tag]["summary"], after["results"][tag]["summary"]
        lines.append(f"{tag}: passed {b['passed']}/{b['cases']} -> {a['passed']}/{a['cases']}")
        for kind in sorted(set(b["by_kind"]) | set(a["by_kind"])):
            kb, ka = (
                b["by_kind"].get(kind, {"passed": 0, "cases": 0}),
                a["by_kind"].get(kind, {"passed": 0, "cases": 0}),
            )
            mark = "" if kb == ka else ("  worse" if ka["passed"] < kb["passed"] else "  better")
            lines.append(
                f"  {kind}: {kb['passed']}/{kb['cases']} -> {ka['passed']}/{ka['cases']}{mark}"
            )
        fb, fa = b["first_word_ms_median"], a["first_word_ms_median"]
        lines.append(f"  first word (median): {fb} ms -> {fa} ms")
    for tag in sorted(set(before.get("results", {})) ^ set(after.get("results", {}))):
        lines.append(f"{tag}: only in one of the two files")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    before, after = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args)
    print("\n".join(compare(before, after)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
