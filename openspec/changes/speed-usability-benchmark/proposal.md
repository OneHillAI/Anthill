# Speed/usability benchmark against real LLM usage patterns

Full spec: `docs/specs/speed-usability-benchmark.md`.

## Why

No way existed to test Anthill's real-world speed and usability against anything grounded in actual
LLM usage - no representative prompt set, no harness to time Anthill's chat pipeline end to end.
Researched what's actually out there before building anything: Anthropic's Economic Index/Clio and
OpenAI's "How People Use ChatGPT" (NBER paper) give real usage proportions from ~1M+ real conversations
each; WildChat-1M and LMSYS-Chat-1M are real-conversation datasets sampleable directly; Arena-Hard-Auto
is the current best-practice curated 500-prompt benchmark built from that same real distribution. None
of these measure latency by themselves - this closes that gap.

## What changes

- `tools/bench/fixtures/real_usage_sample.jsonl` (new, 300 real prompts sampled from WildChat-1M at
  spread-out offsets, English/non-toxic/plausible-length filtered) and `arena_hard_v0.1_questions.jsonl`
  (new, the full unmodified 500-prompt Arena-Hard-Auto v0.1 set) - 800 prompts total.
- `tools/bench/fetch_prompts.py` (new): re-samples the real-usage set via HuggingFace's public
  datasets-server API; LMSYS-Chat-1M is gated (needs an accepted-terms token) and is skipped
  gracefully with WildChat topped up to compensate when no token is supplied.
- `anthill/web_client.py`: `OrgClient.stream()` gains a new, backward-compatible `escalate_org: bool =
  False` parameter mirroring the server's existing per-turn escalation parameter (#278) - lets a script
  force a turn onto the connected org/cloud/provider backend without a natural-language redo phrase.
- `tools/bench/run_timing.py` (new): drives a live Anthill server via `OrgClient` (the same client
  `anthill chat --org` already uses) and times each prompt's time-to-first-token and total completion
  time, one fresh conversation per prompt so timings stay independently comparable.
- `tools/bench/compare_runs.py` (new): prints a side-by-side comparison table from two or more timing
  result files (e.g. local vs escalated-to-provider on the same account).

## Guardrails (do NOT touch)

- No change to `anthill chat --org`'s interactive REPL beyond the new `escalate_org` parameter on
  `OrgClient.stream()` itself - not wired into any `/` command there.
- No attempt to run this against a live instance from this environment - no running Anthill server is
  available here; handed off to a session that has one, for actual execution.
- No automated quality scoring (LLM-as-judge) added - the Arena-Hard-Auto prompt file is fetched and
  available for that later, but scoring itself is out of scope for this change.
