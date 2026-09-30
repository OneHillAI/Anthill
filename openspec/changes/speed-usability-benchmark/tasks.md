# Tasks

- [x] Research: confirm what real-usage taxonomies/datasets/benchmarks actually exist (Anthropic
  Economic Index/Clio, OpenAI's "How People Use ChatGPT", WildChat-1M, LMSYS-Chat-1M,
  Arena-Hard-Auto/MT-Bench/AlpacaEval) via web search, with sources - before writing any code.
- [x] `tools/bench/fetch_prompts.py`: sample real first-turn prompts from WildChat-1M via HF's public
  datasets-server API (spread-out offsets, English/non-toxic/length filters, deduplicated); graceful
  gated-dataset handling for LMSYS-Chat-1M (`--hf-token`/`$HF_TOKEN`, tops up WildChat when absent).
  Ran it: `tools/bench/fixtures/real_usage_sample.jsonl` (300 prompts).
- [x] Fetch `tools/bench/fixtures/arena_hard_v0.1_questions.jsonl` (Arena-Hard-Auto v0.1, 500 prompts,
  verbatim from its GitHub repo).
- [x] `anthill/web_client.py`: add `escalate_org` param to `OrgClient.stream()`; tests
  (`tests/test_web_client.py`) proving it's included/defaulted correctly in the request.
- [x] `tools/bench/run_timing.py`: load both prompt schemas; time each turn (TTFT + total) via
  `OrgClient`, one fresh conversation per prompt; per-prompt JSONL output + grouped summary.
- [x] `tools/bench/compare_runs.py`: side-by-side comparison table across result files.
- [x] Tests (`tests/test_bench_timing_harness.py`): prompt loading (both schemas, multi-file, limit),
  row filtering (`_first_user_turn`), timing capture against a fake client (TTFT vs total, error
  handling, new-conversation failure, escalate/plane pass-through), stats/summary grouping,
  compare_runs' file loading.
- [x] `tools/bench/README.md`: what's here, how to run it, what it measures.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2440 passed, 10
  skipped, no regressions).
- [ ] Hand off to a session with a live running Anthill instance to actually execute the harness and
  report real numbers - not done here, no live instance available in this environment.
