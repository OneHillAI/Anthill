# Spec: Benchmark a model from the picker

Status: implemented. Lane: `pillar:model`. Issue: #276.

## Problem

Anthill already has an eval harness (`lifecycle/evaluate.py`: `evaluate_models`) that scores two models
against a set of reference answers, but it was only reachable from training internals. From the Local
model page an admin could install a candidate model but had no way to answer the one question that
matters before switching: **is this model actually better than the one I run now, on my own data?**

## Policy

- The Local model page (`/models`) shows a **Benchmark** button next to each installed model that is not
  the current one, but only when the org has at least 3 approved (gold) answers - a benchmark is
  meaningless without reference answers to score against.
- `POST /models/benchmark` (admin only) validates that the candidate is installed, is not the current
  model, and that `_gold` yields >= 3 examples; otherwise it redirects back with
  `?error=benchmark` / `?error=no_gold` (the candidate tag is URL-encoded in the success redirect). On
  success it persists the run, audit-logs `model.benchmark_started`, and runs the eval in the background
  via `_spawn` so the request returns immediately.
- Run state is stored in the DB as a JSON blob on `OrgSettings.benchmark_state` (running / candidate /
  summary / winner / error), written from the background thread via a fresh session - the same pattern
  as `local_model_pulling` / `_set_model_pulling`. This keeps it shared across workers and avoids
  in-process global state; it is transient (overwritten each run, last-writer-wins).
- One benchmark at a time per org: if a run is already in flight, `POST /models/benchmark` does not start
  a second (heavy) eval - it redirects to the in-progress run instead.
- The eval reuses the existing harness: `evaluate_models(OllamaBackend(url, current), current,
  candidate, examples)` over the org's gold `(instruction, output)` pairs. When it finishes, the state
  holds `summary`, `winner`, and `error`.
- `GET /models/benchmark-status` returns the current state as JSON. The page polls it while a run is in
  flight and reloads to show the result banner (winner + summary), mirroring the existing pull-status
  poller. The score is mean cosine similarity to the gold answers and is labelled advisory.

## Acceptance criteria

- With >= 3 gold answers and an installed candidate != current, `POST /models/benchmark` starts a run,
  redirects with `benchmarking=<tag>`, and once complete `OrgSettings.benchmark_state` carries the winner
  and summary from `evaluate_models`.
- No gold answers -> `error=no_gold`; candidate == current (or not installed) -> `error=benchmark`; no
  run is started in either case.
- The `/models` page renders the Benchmark button only when `gold_count >= 3` and a current model is set.
- The status route reports whether a run is in progress.
- Covered by `tests/test_model_benchmark.py`. Because tests have no reachable Ollama, `evaluate_models`
  is mocked; the harness itself is unchanged.
