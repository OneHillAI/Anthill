# Spec: a reference-machine benchmark harness and a per-release scoreboard

Status: implemented. Lane: `pillar:model`. Issue: #236.

## Problem

Anthill has no record of how fast it is on any machine, release over release. The open Model-pillar questions (is
Ollama 0.40 with MLX faster than the pinned 0.30.10 at 16 and 24 GB, does a second engine win, which build of a
model gives the most quality per GB, does a larger-than-memory model work at all) each need a repeatable
measurement. `tools/bench/run_timing.py` times whole turns against a running Anthill server with real prompt sets
(product latency); it reads no engine statistics, does not control the context depth, and `compare_runs.py` has no
regression threshold. Anthill's own decode estimate rests on two calibration points on one chip.

## Requirements

1. **Protocol** (`tools/bench/machinebench/protocol.py`). A cool-down (20 s) before every run after the first, one
   discarded warm-up, three repeats, the median, and the spread ((max - min) over the median). A figure whose
   spread is above 10% is `unstable`, a figure the engine did not report is `missing` (never 0), and a run that
   cannot count (wrong prompt depth, a short answer, a cold run that hit the cache) is noted with its reason and
   makes the scenario's figures `unstable`. On macOS the tool checks for AC power (`pmset -g batt`), refuses on
   battery unless `--allow-battery` is given, and records the state either way. It changes no machine setting.
2. **Engine client** (`engine.py`). Talks to a local Ollama's `/api/chat` at temperature 0 with an explicit window
   and a fixed seed, streams, and reads the engine's own counters through `anthill/inference/stats.py` (prefill and
   decode counts and times, load time, cached tokens), plus the time to the first word. It reads `/api/version`,
   `/api/tags` and `/api/ps`. It never pulls, deletes or changes a model: a model that is not installed is reported
   (`ModelMissing`) before anything runs. The one state it touches is unloading a model for the cold-load scenario.
3. **Scenarios** (`scenarios.py`), each one run per repeat:
   - `depth_0` and `depth_4k`: prefill and decode speed with about 0 and 4,000 tokens of prompt. The characters
     per token are calibrated from the engine's own counts with two probe prompts; the real prompt length is read
     back, reported, and a run more than 10% off its target is rejected.
   - `wiki_cold` and `wiki_warm`: a fixed wiki prompt of about 2,500 tokens (`fixtures/scoreboard_wiki_prompt.json`,
     built with the real `anthill.wiki.prompts.answer_question` from three synthetic pages plus two more). Cold has a
     unique first token; warm sends the same prompt twice and measures the second. Reports first-word time, prefill
     time and speed, and the cached tokens when the engine reports them.
   - `cold_load`: unload, then one short call: load time and first-word time.
   - `memory_swap`: 20 short turns, then the engine's allocation and the part in GPU memory (`/api/ps`), and the
     swap-outs over the turns (macOS `vm_stat`, Linux `/proc/vmstat`). One reading, not repeated.
   Every prompt starts with a unique token so the engine's prefix cache cannot skip the work being measured, and a
   cold run that the engine reports as cached (more than 5% of its prompt) is rejected.
4. **Result file** (`results.py`). `benchmarks/scoreboard/<anthill-release>/<machine-id>.json`: schema version,
   release, time, the machine (chip, GPU cores, memory, system, Anthill's bandwidth figure, power state), the
   engine name and version, each model's tag and digest, the protocol, and per scenario its parameters, the
   median, spread, samples and status of every figure, and its notes.
5. **Comparison** (`compare_scoreboard.py`, `results.compare`). Lists every median that got worse by more than 15%
   (speeds `*_tok_s` lower, times `*_ms` and `swap_outs` higher), only for figures that were `ok` in both files.
   Report-only: the exit status is 0 whatever it finds.
6. **Documents.** `docs/reference-machines.md` (the machines, how to take a result), a section in
   `tools/bench/README.md`, `benchmarks/scoreboard/README.md`.

The code lives under `tools/bench` (not `scripts/`, which is protected) in a package, `machinebench`, so that its
module names cannot collide with anything else when loaded by path. `tools/bench/run_timing.py` and
`compare_runs.py` are unchanged.

## The first result

`benchmarks/scoreboard/1.1.1/apple-m4-16gb.json`: Apple M4 (10 GPU cores, 16 GB, 120 GB/s), on AC power, Ollama
0.40.7, `qwen2.5:3b` (digest `357c53fb659c`), recorded 2026-11-17. Every figure is `ok` (spread at most 5.3%, apart
from the two the jitter rules cover); medians of three:

| Scenario | Figure | Median |
|---|---|---|
| depth_0 (42 tokens) | decode | 46.5 tok/s |
| | first word | 157 ms |
| depth_4k (4,051 tokens) | prefill | 490 tok/s |
| | decode | 42.8 tok/s |
| | first word | 8.3 s |
| wiki_cold (2,542 tokens) | prefill | 510 tok/s |
| | first word | 5.05 s |
| wiki_warm (2,539 of 2,540 cached) | first word | 45 ms |
| cold_load | load | 579 ms |
| | first word | 720 ms |
| memory_swap (20 turns) | allocated, in GPU memory | 2.41 GB, 2.41 GB |
| | swap-outs | 0 |

An earlier run of the same tool gave the same figures to within about 2% (decode 47.0, prefill at 4,000 tokens
497, cold load 578). Two earlier runs were discarded because they exposed flaws in the harness itself, each now
covered by a test: the unique token sat behind the model's default system text, which the engine cached (every
depth-0 run was rejected); calibration used a junk filler, so "4,000 token" prompts were 1,709 tokens; and a 45 ms
warm first word and a cold prompt's 3 or 4 cached template tokens were flagged `unstable` on percentages of a tiny
range, so times within 5 ms and token counts within 8 are now treated as jitter.

## Known limits

- Only the M4 16 GB has a committed result. The 24 GB Mac needs a machine this work did not have.
- The first committed run is one model (`qwen2.5:3b`), one engine version (the one on the machine, 0.40.7) and
  one run; it is a baseline, not a comparison. The pinned 0.30.10 and the MLX runner are not measured here.
- Thinking models are measured as they are: their reasoning tokens count as decode tokens.
- Depth is built from repeated filler text and calibrated per model; the reported prompt length is the engine's
  count, and an off-target run is rejected, not corrected.
- The pinned engine (0.30.10) reports no cached-token count (see `docs/specs/ollama-engine-upgrade-gate.md`), so
  `cached_tokens` is `missing` there and the cold-run cache guard cannot fire; the unique first token is the only
  protection on that version.
- `wiki_warm`'s prefill speed is not a speed: the warm prompt processes about one token, so the figure is fixed
  overhead. Read `prefill_ms` and `first_word_ms` for the warm prompt.
- Thermal state is not controlled beyond the 20 s cool-down and the AC check; a spread above 10% is how a hot or
  busy machine shows up. Times whose whole range is within 5 ms, and token counts within 8, are not judged by
  percentage (`Protocol.ms_noise_floor`, `token_noise_floor`).
- `swap_outs` is the system-wide counter, so another program swapping during the run is counted.
- Nothing here changes a recommendation, a default or the model picker; a number is a record, not a rule.

## Acceptance

- `tests/test_bench_protocol.py` and `tests/test_scoreboard.py` (new, fake engine): median and spread; a warm-up
  discarded; `unstable` above 10% and exactly at 10% `ok`; a missing figure is not 0; an invalid run is noted; the
  AC refusal; depth read back and an off-target run rejected; cold prompts carry distinct first tokens; a cold run
  that hits the cache is rejected; prefill speed excludes cached tokens; the schema and the file path; the 15%
  rule in both directions and exactly 15% not flagged; unstable figures skipped; and a missing model is reported
  before any call, with nothing to pull.
- One real run on the M4 16 GB is committed with the Ollama version, the model digest and the power state.
- `docs/reference-machines.md` lists the machines.
