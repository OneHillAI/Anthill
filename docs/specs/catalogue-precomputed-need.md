# Spec: the picker reads a precomputed memory need per model

Status: implemented (first slice of the catalogue support layer). Lane: `pillar:model`. Issue: #320.

## Problem

Whether a model fits a Mac is decided by guessing from its parameter count. `resident_gb` in `anthill/hosting/sizing.py`
is weights (0.55 GB per billion parameters) + a flat 0.1 GB per 1,000 tokens of context + a flat 2.5 GB runner
allowance. It feeds two rules: the parameter ceiling `max_params_b`, which `recommend`, `recommend_by_family` (and so the
first-run suggestion) compare a model's parameter count against, and `fit_tier`, which the model picker view uses to grey
out and preselect, and which the server-side guard uses to refuse a pull.

The guess is wrong in both directions. Measured on an M4 16 GB: a 9B model with the bge-m3 embedder resident used 9.8 GB
(roadmap #97) against 8.25 GB from the formula; the GGUF build of Qwen3.5 4B (`qwen3.5:4b-q4_K_M`) measured 3.38 GB,
plus the embedder's 0.69 GB, 4.07 GB, against 5.5 GB from the formula. The exact per-model memory module (#230,
`model_memory.py`) has no caller.

The direction: compute each model's memory once, up front, in the catalogue, and let the device only compare that number
with its own memory. The catalogue is built separately; this change is the place in the app that reads the number.

## Requirements

1. A catalogue row may carry `need_gb`, a number: the memory in GB the model occupies while running on Apple Silicon at
   the reference setup (an 8,192-token window, one stream, the embedder resident). It is the same quantity `resident_gb`
   approximates, so the existing thresholds apply to it unchanged (too large above 66% of memory; "recommended" only if at
   least 7 GB stay free; otherwise "tight").
2. A value that is not a plausible figure (not a number, a bool, zero, negative, not finite, or 2,048 or more) is ignored: the row is then
   judged exactly as before. It never raises and never changes a verdict.
3. `sizing.model_fit_tier(model, mem_gb, ...)` judges a model by its `need_gb` when it has a valid one and the question
   is the reference setup on Apple Silicon (8,192-token window, one stream); in every other case (no need, a GPU box, a
   different window or stream count) it returns exactly what `fit_tier` returns for the parameter count.
4. `recommend` and `recommend_by_family` decide "fits" with `sizing.model_fits_memory`, which uses the need in the same
   conditions and the parameter ceiling otherwise. Both draw the line at the same place (66% of memory).
5. The three places in `anthill/web/app.py` that called `fit_tier` with a parameter count (the picker view, the local
   model options and the pull guard) call `model_fit_tier`.
6. No shipped catalogue row has `need_gb` yet, so every current verdict is unchanged.

## What `need_gb` means (for whoever builds the catalogue)

The memory the machine gives up while the model answers: the chat model as the engine holds it (weights, its cache for an
8,192-token window, the runner's working memory) plus the embedder, on a Mac, with the build the row pulls. A measured
figure beats a computed one: load the build, run a long mixed session, and take the engine's own resident size
(`/api/ps`) for the chat model plus the embedder. Examples measured on an M4 16 GB (llama.cpp builds): `qwen3.5:4b-q4_K_M`
3.38 + 0.69 = 4.07 GB; `qwen2.5:3b` 2.41 + 0.69 = 3.10 GB; `nemotron-3-nano:4b` 2.97 + 0.69 = 3.66 GB. Take it from a
build whose memory stays flat over a long chat: the MLX build of Qwen3.5 4B grew from 4.07 GB to 12.59 GB over 48 turns,
so its number would be wrong by the next conversation. Extra fields (how it was measured, on which engine) are ignored by
the app and welcome.

## Not changed here

Which model is the default, the scores, the speed rule, GPU boxes and the cluster path, the engine, which build a row pulls
and engine compatibility per row (separate slices).

## Acceptance

- `tests/test_catalogue_precomputed_need.py` passes: a valid need decides the tier (boundaries at 16 GB), an invalid one
  is ignored, the need counts only for the reference setup on Apple Silicon, the recommendations and the pull guard use
  it, and for every shipped row and memory tier (8, 16, 24, 32, 64 GB) the fit tier, the per-family picks and the fits
  list are identical to the old calculation.
- The neighbouring catalogue, sizing, picker and guard tests pass; the full preflight passes.
