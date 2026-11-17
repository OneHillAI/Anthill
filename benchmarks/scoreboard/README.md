# Scoreboard

One JSON file per machine per Anthill release: `<release>/<machine>.json`, written by
`tools/bench/run_scoreboard.py`. Each file holds the machine, the Ollama version, every model's digest, the
protocol, and for each scenario the median, spread, raw samples and status of every figure. How to take a result
and which machines count: `docs/reference-machines.md`. Compare two with `tools/bench/compare_scoreboard.py`.
