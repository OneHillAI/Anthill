# Reference machines

The machines Anthill's speed is measured on, so that a number means the same thing from one release to the next.
The harness is `tools/bench/run_scoreboard.py` (spec: `docs/specs/reference-machine-scoreboard.md`); a result is
one file per machine per release in `benchmarks/scoreboard/<release>/<machine>.json`.

## The machines

| Machine | Chip, memory | Memory bandwidth | Status |
|---|---|---|---|
| `apple-m4-16gb` | Apple M4, 10 GPU cores, 16 GB | 120 GB/s | first run committed (see `benchmarks/scoreboard/`) |
| 24 GB Mac | a Pro-class chip, 24 GB | to be read from the result | needs the founder's machine |
| 8 GB Mac | | | later |
| 32 GB Mac | | | later |
| Linux CPU box | | | later |
| NVIDIA 16 GB and 24 GB | | | later |

The bandwidth column is Anthill's own table of Apple's published figures (`anthill/hosting/sizing.py`), recorded
in each result for reference. A machine that is not an Apple Silicon Mac records its chip name and memory only.

What each machine can run is not listed here yet: it comes from the scoreboard, not from an estimate.

## How a result is taken

Run it the same way every time, so two results can be compared.

1. Plug the machine in. The tool refuses to run on battery (a laptop slows itself down on battery); `--allow-battery`
   overrides it and the result records the power state.
2. Close everything else. Leave the machine alone while it runs, about ten minutes per model.
3. Have the model installed already. The tool never pulls or deletes a model.
4. Run: `python tools/bench/run_scoreboard.py --model qwen2.5:3b`. `qwen2.5:3b` is the development model in
   `AGENTS.md`, small enough for every machine above.
5. Commit the file it wrote. It carries the Ollama version, each model's digest and the power state; a number
   without them is not committed.

The protocol: one discarded warm-up, then three runs with a 20 second cool-down between them; the figure is the
median and its spread is (max - min) over the median. A spread above 10% is recorded as `unstable`, not hidden.
Every prompt starts with a unique token so the engine's cache cannot skip the work being measured.

## Comparing two results

`python tools/bench/compare_scoreboard.py old.json new.json` lists every median that got worse by more than 15%
(speeds lower, times higher, swap-outs up). It only reports: its exit status is 0 whatever it finds.

## Adding a machine

Add a row above, run the tool on it, and commit the file. Say in the row what is unusual about it (a laptop that
throttles, a shared box).
