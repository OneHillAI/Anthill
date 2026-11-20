# The picker reads a precomputed memory need per model

Full spec: `docs/specs/catalogue-precomputed-need.md`. Issue: #320. Lane: `pillar:model`.

## Why

Fit is guessed from a model's parameter count with flat constants, and the guess is wrong in both directions (a 9B
model measured 9.8 GB against 8.25 GB from the formula; the 4B GGUF build measured 4.07 GB against 5.5 GB). The direction
is to compute each model's memory once in the catalogue and let the device compare it with its own memory.

## What changes

An optional `need_gb` per catalogue row, read into the model and used, when valid, by one helper behind `recommend`,
`recommend_by_family`, the picker view, the local model options and the pull guard. A row without a valid one is judged
exactly as before, and no shipped row has one, so no verdict changes.

## Guardrails (do NOT touch)

- Every current verdict, the selection rules, scores, thresholds, GPU boxes, the engine, the pull path.
