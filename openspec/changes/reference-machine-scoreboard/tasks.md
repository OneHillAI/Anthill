# Tasks

- [x] `tools/bench/machinebench/protocol.py`: cool-down, discarded warm-up, three repeats, median, spread, the 10%
  `unstable` rule, the AC-power check.
- [x] `tools/bench/machinebench/engine.py`: the Ollama client (counters, first word, never pulls).
- [x] `tools/bench/machinebench/scenarios.py`: depth 0 and 4,000, the fixed wiki prompt cold and warm, cold load,
  memory and swap-outs.
- [x] `tools/bench/fixtures/scoreboard_wiki_prompt.json` and its generator.
- [x] `tools/bench/machinebench/results.py` and `machine.py`: the machine descriptor, the schema, the writer, the
  report-only 15% comparison; `run_scoreboard.py` and `compare_scoreboard.py` as the entry points.
- [x] `tools/bench/README.md` section, `docs/reference-machines.md`, `benchmarks/scoreboard/README.md`.
- [x] `tests/test_bench_protocol.py` and `tests/test_scoreboard.py` with a fake engine.
- [x] A real run on the M4 16 GB committed under `benchmarks/scoreboard/1.1.1/`.
- [ ] A run on a 24 GB Mac: needs that machine from the founder; not required for this change.
