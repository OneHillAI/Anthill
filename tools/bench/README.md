# Speed/usability benchmark

Answers "how fast and usable is Anthill against the kinds of questions people actually ask an
LLM?" - grounded in real usage research, not made-up test prompts.

## What's here

- **`data/real_usage_sample.jsonl`** (300 prompts) - real first-turn user messages sampled from
  [WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) (open, ~838K real ChatGPT
  conversations). LMSYS-Chat-1M is a second real-conversation dataset but is access-gated on
  HuggingFace; `fetch_prompts.py --hf-token` can pull from it too once someone has accepted its terms.
- **`data/arena_hard_v0.1_questions.jsonl`** (500 prompts) - the full [Arena-Hard-Auto
  v0.1](https://github.com/lmarena/arena-hard-auto) set: hard prompts curated from the same real
  Chatbot Arena distribution via an automated pipeline, the current standard for a repeatable,
  human-correlated model-quality score.
- **`fetch_prompts.py`** - re-samples the real-usage set (re-run to refresh; not run automatically).
- **`run_timing.py`** - drives a live Anthill server exactly like a Chat user would (via
  `anthill.web_client.OrgClient`, the same client `anthill chat --org` uses) and times each turn.
- **`compare_runs.py`** - prints a side-by-side table from two or more `run_timing.py` result files.

Full grounding for why these two sources specifically: `docs/specs/speed-usability-benchmark.md`.

## Running it

Needs a running Anthill server and an account with a model actually pulled/configured (local, your
own RunPod/Lambda, or a connected inference provider) - this tool can't provision that for you, only
measure it once it's there.

```bash
export ANTHILL_ORG_URL=http://localhost:8000
export ANTHILL_EMAIL=you@example.com
export ANTHILL_PASSWORD=...

# Local tier baseline (whatever the account's default compute is)
python tools/bench/run_timing.py \
  --prompts tools/bench/fixtures/real_usage_sample.jsonl \
  --prompts tools/bench/fixtures/arena_hard_v0.1_questions.jsonl \
  --limit 50 --label local-tier \
  --output tools/bench/results/local-tier.jsonl

# The SAME account escalated to its connected org/cloud/provider backend, per turn (#278's
# redo-with-provider mechanism) - needs an endpoint already connected on that account.
python tools/bench/run_timing.py \
  --prompts tools/bench/fixtures/real_usage_sample.jsonl \
  --prompts tools/bench/fixtures/arena_hard_v0.1_questions.jsonl \
  --limit 50 --label provider-tier --escalate \
  --output tools/bench/results/provider-tier.jsonl

python tools/bench/compare_runs.py tools/bench/results/local-tier.jsonl tools/bench/results/provider-tier.jsonl
```

`--limit` defaults to 20 - raise it for a fuller run once a small batch looks right; the full combined
set is 800 prompts (300 real-usage + 500 Arena-Hard-Auto), well inside the 100-1,000 target range.

Each `run_timing.py` invocation opens ONE fresh conversation per prompt (not one growing thread) so a
later prompt's timing is never skewed by an earlier prompt's context length - matching how most real
usage is a fresh question (per the OpenAI/Anthropic usage research `speed-usability-benchmark.md`
cites), and keeping every prompt's number independently comparable.

## What it measures

Per prompt: time-to-first-token (TTFT) and total completion time, plus response length and any error.
Grouped in the printed summary by source (wildchat / arena-hard-v0.1 category) and, across runs, by
the `--label` you gave each one. It measures ANTHILL's real end-to-end latency for whichever compute
tier the target account is configured for - it does not itself switch tiers or provision anything.
