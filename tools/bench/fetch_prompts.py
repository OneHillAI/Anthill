"""Build the real-usage prompt sample for the Anthill speed/usability benchmark.

Pulls a spread of real first-turn user prompts from two public, real-conversation LLM datasets via
HuggingFace's datasets-server API (no bulk download - only the sampled rows are fetched):

- WildChat-1M (allenai/WildChat-1M): ~838K real ChatGPT conversations, collected via a free-to-use
  hosted API in exchange for logging - closest to genuine everyday usage.
- LMSYS-Chat-1M (lmsys/lmsys-chat-1m): ~1M conversations from Chatbot Arena/the Vicuna demo - skews
  slightly more toward people deliberately comparing models, but still real prompts.

Sampled at spread-out offsets (not just the first N rows) to avoid time/topic clustering. Filtered to
English, non-toxic (dataset-provided flags), and a plausible single-message length (10-800 chars) -
long enough to be a real task, short enough that a first turn isn't itself a multi-paragraph document
paste skewing the sample. Deduplicated by prompt text. Writes one combined JSONL:
``tools/bench/fixtures/real_usage_sample.jsonl``, each line ``{"id", "source", "language", "prompt"}``.

Re-run this to refresh the sample; it is NOT run automatically - the checked-in file is what
run_timing.py reads by default.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_API = "https://datasets-server.huggingface.co/rows"
_PAGE = 100  # datasets-server's per-request row cap


def _fetch_page(dataset: str, offset: int, length: int, *, hf_token: str = "") -> list[dict]:
    qs = urllib.parse.urlencode(
        {
            "dataset": dataset,
            "config": "default",
            "split": "train",
            "offset": offset,
            "length": length,
        }
    )
    headers = {"User-Agent": "anthill-bench/1"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    req = urllib.request.Request(f"{_API}?{qs}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("rows", [])


def _first_user_turn(row: dict, *, conv_key: str) -> dict | None:
    conv = row.get("row", {}).get(conv_key) or []
    if not conv:
        return None
    first = conv[0]
    if (first.get("role") or "").lower() != "user":
        return None
    return first


def _sample_dataset(
    dataset: str,
    *,
    source: str,
    conv_key: str,
    total_rows: int,
    n: int,
    seed: int,
    hf_token: str = "",
) -> list[dict]:
    # Fetch full _PAGE-sized chunks at spread-out, non-overlapping offsets (not one row per request -
    # datasets-server charges the same latency per call regardless of `length`, so a handful of 100-row
    # pages covers the target count in far fewer round-trips than one request per row).
    rng = random.Random(seed)
    n_pages = max(1, (n // 20) + 3)  # ~20 keepers/page after filtering, +buffer for a thin page
    max_offset = max(1, total_rows - _PAGE)
    offsets = sorted(rng.sample(range(0, max_offset, _PAGE), k=min(n_pages, max_offset // _PAGE)))
    out: list[dict] = []
    seen: set[str] = set()
    for offset in offsets:
        if len(out) >= n:
            break
        try:
            rows = _fetch_page(dataset, offset, _PAGE, hf_token=hf_token)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print(
                    f"  [gated] {dataset} needs an HF token with its terms accepted "
                    "(pass --hf-token, or set HF_TOKEN) - skipping this source.",
                    file=sys.stderr,
                )
                return out
            print(f"  [skip] offset {offset}: {e}", file=sys.stderr)
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  [skip] offset {offset}: {e}", file=sys.stderr)
            continue
        for row in rows:
            if len(out) >= n:
                break
            turn = _first_user_turn(row, conv_key=conv_key)
            if not turn:
                continue
            text = (turn.get("content") or "").strip()
            if not (10 <= len(text) <= 800):
                continue
            if (turn.get("language") or row.get("row", {}).get("language") or "").lower() not in (
                "english",
                "en",
                "",
            ):
                continue
            if turn.get("toxic") or row.get("row", {}).get("toxic"):
                continue
            if text in seen:
                continue
            seen.add(text)
            out.append(
                {
                    "id": f"{source}-{row.get('row_idx', offset)}",
                    "source": source,
                    "language": turn.get("language") or "english",
                    "prompt": text,
                }
            )
        time.sleep(0.2)  # be polite to the shared public API
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--per-source",
        type=int,
        default=150,
        help="target prompts from WildChat (open, no gate); LMSYS-Chat-1M gets the same target "
        "IF --hf-token/HF_TOKEN unlocks it, otherwise it's skipped and WildChat alone stands",
    )
    ap.add_argument("--seed", type=int, default=20260806)
    ap.add_argument(
        "--hf-token",
        default=os.environ.get("HF_TOKEN", ""),
        help="HuggingFace token with lmsys/lmsys-chat-1m's terms accepted (gated dataset); "
        "defaults to $HF_TOKEN. WildChat-1M needs no token.",
    )
    ap.add_argument(
        "--output",
        default="tools/bench/fixtures/real_usage_sample.jsonl",
        help="output JSONL path",
    )
    args = ap.parse_args()

    print(f"Sampling ~{args.per_source} prompts from WildChat-1M...", file=sys.stderr)
    wildchat = _sample_dataset(
        "allenai/WildChat-1M",
        source="wildchat",
        conv_key="conversation",
        total_rows=837_989,
        n=args.per_source,
        seed=args.seed,
    )
    print(f"  got {len(wildchat)}", file=sys.stderr)

    print(f"Sampling ~{args.per_source} prompts from LMSYS-Chat-1M...", file=sys.stderr)
    lmsys = _sample_dataset(
        "lmsys/lmsys-chat-1m",
        source="lmsys",
        conv_key="conversation",
        total_rows=1_000_000,
        n=args.per_source,
        seed=args.seed + 1,
        hf_token=args.hf_token,
    )
    print(f"  got {len(lmsys)}", file=sys.stderr)
    if not lmsys and not args.hf_token:
        # Compensate so the combined sample still lands in the target range without the gated source.
        print("Topping up WildChat to compensate for the skipped gated source...", file=sys.stderr)
        top_up = _sample_dataset(
            "allenai/WildChat-1M",
            source="wildchat",
            conv_key="conversation",
            total_rows=837_989,
            n=args.per_source,
            seed=args.seed + 2,
        )
        seen = {p["prompt"] for p in wildchat}
        for p in top_up:
            if p["prompt"] not in seen:
                seen.add(p["prompt"])
                wildchat.append(p)
        print(f"  wildchat total now {len(wildchat)}", file=sys.stderr)

    combined = wildchat + lmsys
    with open(args.output, "w") as f:
        for row in combined:
            f.write(json.dumps(row) + "\n")
    print(f"Wrote {len(combined)} prompts to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
