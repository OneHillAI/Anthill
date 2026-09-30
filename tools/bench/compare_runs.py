"""Compare two or more run_timing.py result files (one per compute tier) side by side.

Usage:
    python tools/bench/compare_runs.py \\
        tools/bench/results/local-tier.jsonl \\
        tools/bench/results/your-cloud-tier.jsonl \\
        tools/bench/results/provider-tier.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics


def _load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _stats(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "p95": None}
    sorted_v = sorted(values)
    p95_idx = min(len(sorted_v) - 1, round(0.95 * (len(sorted_v) - 1)))
    return {
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(sorted_v[p95_idx], 3),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", nargs="+", help="run_timing.py --output JSONL files")
    args = ap.parse_args(argv)

    rows = []
    for path in args.results:
        data = _load(path)
        label = data[0]["label"] if data else path
        errors = sum(1 for d in data if d.get("error"))
        ttfts = [d["ttft_s"] for d in data if d.get("ttft_s") is not None]
        totals = [d["total_s"] for d in data if d.get("total_s") is not None]
        rows.append(
            {
                "label": label,
                "file": path,
                "n": len(data),
                "errors": errors,
                "ttft_s": _stats(ttfts),
                "total_s": _stats(totals),
            }
        )

    header = f"{'label':<20}{'n':>6}{'errors':>8}{'ttft mean':>12}{'ttft p95':>12}{'total mean':>12}{'total p95':>12}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['label']:<20}{r['n']:>6}{r['errors']:>8}"
            f"{_fmt(r['ttft_s']['mean']):>12}{_fmt(r['ttft_s']['p95']):>12}"
            f"{_fmt(r['total_s']['mean']):>12}{_fmt(r['total_s']['p95']):>12}"
        )
    return 0


def _fmt(v: float | None) -> str:
    return f"{v}s" if v is not None else "-"


if __name__ == "__main__":
    raise SystemExit(main())
