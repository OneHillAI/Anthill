"""Speed/usability benchmark: run a sampled prompt set through a live Anthill server and time it.

Drives the SAME HTTP API a real Chat user hits, via ``anthill.web_client.OrgClient`` (already used by
`anthill chat --org`, unit-tested against a mock transport): log in, open one conversation per prompt
(so a later prompt's timing is never skewed by an earlier prompt's growing context - matches how most
real usage is a fresh question, per the Anthropic/OpenAI usage research this sample is built from), and
time each turn's stream from request start to first token (TTFT) and to completion.

This measures ANTHILL's end-to-end latency for whichever compute tier the target account is currently
configured for (local / your-cloud / an inference provider) - it does not switch tiers itself. To
compare tiers, either run this against separate accounts each configured for a different tier, or use
``--escalate`` on an account that has a local default AND a connected org/cloud/provider endpoint, to
force each turn onto the connected backend (the same "redo with provider" mechanism Chat's escalation
engine uses, #278) alongside a plain run for the local baseline.

Usage:
    python tools/bench/run_timing.py --server http://localhost:8000 \\
        --email you@example.com --password ... \\
        --prompts tools/bench/fixtures/real_usage_sample.jsonl \\
        --prompts tools/bench/fixtures/arena_hard_v0.1_questions.jsonl \\
        --limit 50 --label local-tier --output tools/bench/results/local-tier.jsonl

Credentials also read from ANTHILL_ORG_URL / ANTHILL_EMAIL / ANTHILL_PASSWORD (matching `anthill chat
--org`'s existing convention), so a real run needs no secrets on the command line.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass

from anthill.web_client import OrgClient, OrgClientError


@dataclass
class PromptResult:
    id: str
    source: str
    prompt_chars: int
    label: str
    ttft_s: float | None = None
    total_s: float | None = None
    response_chars: int = 0
    error: str = ""


def load_prompts(paths: list[str], *, limit: int | None = None) -> list[dict]:
    """Read one or more JSONL prompt files. Accepts both this tool's own schema
    ({"id","source",...,"prompt"}) and Arena-Hard-Auto's ({"uid","category","subcategory","prompt"})."""
    out: list[dict] = []
    for path in paths:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                out.append(
                    {
                        "id": row.get("id") or row.get("uid") or f"{path}:{len(out)}",
                        "source": row.get("source") or row.get("category") or path,
                        "prompt": row["prompt"],
                    }
                )
    if limit is not None:
        out = out[:limit]
    return out


def time_one_turn(
    client: OrgClient, prompt: dict, *, label: str, plane: str, escalate: bool, web: bool
) -> PromptResult:
    result = PromptResult(
        id=prompt["id"], source=prompt["source"], prompt_chars=len(prompt["prompt"]), label=label
    )
    try:
        conv_id = client.new_conversation(plane=plane)
    except OrgClientError as e:
        result.error = f"new_conversation: {e}"
        return result

    t_start = time.monotonic()
    t_first: float | None = None
    chars = 0
    try:
        for kind, payload in client.stream(
            conv_id, prompt["prompt"], web=web, escalate_org=escalate
        ):
            if kind == "token":
                if t_first is None:
                    t_first = time.monotonic()
                chars += len(str(payload))
            elif kind == "error":
                result.error = str(payload)
    except OrgClientError as e:
        result.error = result.error or f"stream: {e}"
    t_done = time.monotonic()

    if t_first is not None:
        result.ttft_s = round(t_first - t_start, 4)
    result.total_s = round(t_done - t_start, 4)
    result.response_chars = chars
    return result


def _summarize(results: list[PromptResult], *, group_by: str) -> dict:
    groups: dict[str, list[PromptResult]] = {}
    for r in results:
        groups.setdefault(getattr(r, group_by), []).append(r)
    summary = {}
    for key, rows in groups.items():
        ttfts = [r.ttft_s for r in rows if r.ttft_s is not None]
        totals = [r.total_s for r in rows if r.total_s is not None]
        errors = sum(1 for r in rows if r.error)
        summary[key] = {
            "n": len(rows),
            "errors": errors,
            "ttft_s": _stats(ttfts),
            "total_s": _stats(totals),
        }
    return summary


def _stats(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "p95": None, "min": None, "max": None}
    sorted_v = sorted(values)
    p95_idx = min(len(sorted_v) - 1, round(0.95 * (len(sorted_v) - 1)))
    return {
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(sorted_v[p95_idx], 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--server", default=os.environ.get("ANTHILL_ORG_URL", ""))
    ap.add_argument("--email", default=os.environ.get("ANTHILL_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("ANTHILL_PASSWORD", ""))
    ap.add_argument(
        "--prompts", action="append", required=True, help="JSONL prompt file; repeatable"
    )
    ap.add_argument("--limit", type=int, default=20, help="cap total prompts run (default 20)")
    ap.add_argument("--label", default="run", help="name for this run, kept in the report")
    ap.add_argument("--plane", default="solo", choices=["solo", "org"])
    ap.add_argument(
        "--escalate",
        action="store_true",
        help="force every turn onto the connected org/cloud/provider backend (#278's redo mechanism) "
        "instead of the account's local default",
    )
    ap.add_argument("--web", action="store_true", help="allow web-search fallback per turn")
    ap.add_argument("--output", default="", help="write per-prompt JSONL results here")
    ap.add_argument("--sleep-between", type=float, default=0.5, help="seconds between prompts")
    args = ap.parse_args(argv)

    if not args.server:
        print("error: --server (or ANTHILL_ORG_URL) is required", file=sys.stderr)
        return 2
    if not args.email or not args.password:
        print(
            "error: --email/--password (or ANTHILL_EMAIL/ANTHILL_PASSWORD) required",
            file=sys.stderr,
        )
        return 2

    prompts = load_prompts(args.prompts, limit=args.limit)
    print(f"Loaded {len(prompts)} prompts. Logging in to {args.server}...", file=sys.stderr)

    client = OrgClient(args.server)
    try:
        client.login(args.email, args.password)
    except OrgClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    results: list[PromptResult] = []
    for i, prompt in enumerate(prompts, 1):
        print(f"[{i}/{len(prompts)}] {prompt['id']}...", file=sys.stderr, end=" ")
        r = time_one_turn(
            client, prompt, label=args.label, plane=args.plane, escalate=args.escalate, web=args.web
        )
        results.append(r)
        status = "ERROR: " + r.error if r.error else f"ttft={r.ttft_s}s total={r.total_s}s"
        print(status, file=sys.stderr)
        if i < len(prompts):
            time.sleep(args.sleep_between)
    client.close()

    if args.output:
        with open(args.output, "w") as f:
            for r in results:
                f.write(json.dumps(r.__dict__) + "\n")
        print(f"Wrote {len(results)} results to {args.output}", file=sys.stderr)

    overall = _summarize(results, group_by="label")
    by_source = _summarize(results, group_by="source")
    print("\n=== Summary ===")
    print(json.dumps({"overall": overall, "by_source": by_source}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
