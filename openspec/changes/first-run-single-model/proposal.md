# First run on a small machine suggests one model, never a council below 64 GB

Full spec: `docs/specs/first-run-single-model.md`. Issue: #327. Lane: `pillar:model`.

## Why

At 8 GB the first-run suggestion was a three-model council (Qwen3.5 2B, Gemma 3 1B, Granite 4.2 3B): three downloads on a
machine that can barely hold one.

## What changes

`suggest_local_setup` considers the three-member council only when the machine has at least 64 GB
(`FIRST_RUN_COUNCIL_MIN_GB`). Below that it returns the single best-fitting model, which is today's fallback.

## Guardrails (do NOT touch)

Engine pin, catalogue rows, the pull path, the manual council builder, scores, UI text.
