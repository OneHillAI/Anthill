# Spec: first run on a small machine suggests one model, never a council below 64 GB

Status: implemented. Lane: `pillar:model`. Issue: #327. Builds on `docs/specs/model-selection.md`.

## Problem

On first run the app calls `suggest_local_setup` (`anthill/hosting/sizing.py`), which tried a three-member local council
first and fell back to one model only when the council did not fit in memory or on disk. Measured on `main` (Apple
Silicon, 2026-11-20): at 8 GB the suggestion was a council of Qwen3.5 2B, Gemma 3 1B and Granite 4.2 3B; at 16, 24, 32 and
64 GB it was one model. A small Mac's first run therefore downloaded three models and asked the machine to run several at
once, when it can barely hold one. The rule: the picker works from the machine's memory, lists only models that can run and
recommends the best fit; a council is for machines with room for it (64 GB and larger).

## Requirements

1. Below `FIRST_RUN_COUNCIL_MIN_GB` (64) `suggest_local_setup` never returns `mode="council"`: it returns the single
   best-fitting model (today's fallback), or no member when nothing fits.
2. From 64 GB it behaves as before: a council only when the three picks fit in memory and on disk (speed is filtered per
   member by `recommend_by_family`).
3. The single-model path, the picker list, the manual council builder (its own floor), the pull path and the catalogue are
   unchanged. No new text.
4. The setup form's accepted suggestion provisions exactly what the suggestion says.

## Acceptance

- At 8, 16, 24, 32, 48 and 63.9 GB (Apple) the suggestion is `single` with one member even when three picks would fit
  (`tests/test_first_run_single_model_327.py`); these fail without the change.
- At 64 and 128 GB a council is still returned where memory and disk allow, and a single model where they do not.
- Accepting the first-run suggestion at 8 GB starts exactly one download and leaves the council members empty.
