# Reference-machine benchmark harness and per-release scoreboard

Full spec: `docs/specs/reference-machine-scoreboard.md`. Issue: #236. Lane: `pillar:model`.

## Why

Anthill has no record of how fast it is on any machine, release over release. Every open Model-pillar question
(is Ollama 0.40 with MLX faster than the pinned 0.30.10, does a second engine win, which build of a model gives the
most quality per GB) needs a repeatable measurement, and what exists does not give one: `tools/bench/run_timing.py`
times whole turns against a server, reads no engine statistics and does not control context depth, and Anthill's
own speed estimate rests on two calibration points on one chip.

## What changes

New tooling under `tools/bench` (not `scripts/`, which is protected), one committed result per machine, and two
documents. No change to how Anthill runs.

- A protocol: cool-down, one discarded warm-up, three repeats, median, spread, and an `unstable` flag above 10%.
- An engine client that reads Ollama's own counters and times the first word, and never pulls or deletes a model.
- Scenarios: decode and prefill at 0 and about 4,000 tokens, a fixed wiki prompt cold and warm, cold load, memory
  and swap-outs over 20 turns. Every cold prompt starts with a unique token.
- One JSON file per machine per release with the Ollama version, model digests, protocol and power state, and a
  report-only comparison that flags a median that got worse by more than 15%.
- `docs/reference-machines.md`.

## Guardrails (do NOT touch)

- The harness never pulls, deletes or changes a model or any machine setting; it refuses to run on battery.
- No protected path. `tools/bench/run_timing.py` and `compare_runs.py` are unchanged.
- Nothing runs in `make test`; the tests use a fake engine.
- A scoreboard number is never used to change a recommendation or a default in the same PR.
