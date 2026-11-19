# Spec: the picker's default is the largest model the catalogue marks "fits" for the machine's memory tier

Status: implemented with catalogue release 1. Lane: `pillar:model`. Issue: #311 (depends on #304). Builds on
`docs/specs/model-catalogue-release-1.md` (the rows carry a precomputed `fit` table) and `docs/specs/model-selection.md`.

## Problem

Catalogue release 1 clears every `intelligence` score (none can be sourced). `sizing.recommend` and
`sizing.recommend_by_family` ranked by `(intelligence, params_b)`, so with all scores 0 the default became the largest
model under the memory ceiling, which depends on the machine's detected speed: on a machine whose speed cannot be
estimated it offered a 70B on a 64 GB Mac and a 32B on a 32 GB Mac, and on a 16 GB Mac it offered Gemma 4 12B, which the
catalogue's own table marks `tight`. The direction (maintainer, 2026-11-19): ordering is simple arithmetic on what fits per
memory tier, computed once up front, with no score.

## Requirements

1. `Model` carries the row's `fit` (per tier: `fits`, `tight`, `too_large`). `not_computed`, unknown tiers and unknown
   values are dropped when the row is read.
2. The machine's memory selects the largest tier not above it (8, 16, 24, 32, 64 GB); below 8 GB there is no tier.
3. For an Apple (unified memory) box with one stream and a window of at most 8k tokens, the default is the model with the
   largest `params_b` whose verdict at that tier is `fits` (ties: larger `active_b`, then catalogue order), among models that
   pass `is_usable_speed`. A `tight` model is never the default; it stays listed as runnable.
4. A row with no verdict at that tier (gated rows with `not_computed`, an older catalogue file, no tier) keeps the
   memory-ceiling rule, so an old or partial catalogue behaves as before. GPU boxes and multi-stream boxes keep the ceiling
   rule: the table was computed for one user on unified memory at an 8k window.
5. `recommend_by_family` uses the same rule within each family. Family order and every other call are unchanged.
6. No score is read to order the default when a verdict exists.

## What the user notices

Defaults from the release-1 table: 8 GB Qwen3.5 4B; 16 GB Qwen3.5 9B (Gemma 4 12B is `tight`, not the default); 24 GB
gpt-oss 20B; 32 GB Nemotron 3 Nano 30B; 64 GB Qwen3.6 35B-A3B. These are the same models or close to what the picker offered
before; the rule no longer depends on a score or on the machine's speed estimate.

## Unchanged

`is_safe_row`, `is_safe_ollama_tag`, `load_catalog`'s trust checks, the signing workflow, the generator, `is_usable_speed`.

## Acceptance

- At 16 GB the default is never a `tight` model; at 8 GB it is the largest `fits` model; a catalogue without `fit` behaves as before.
- `tests/test_picker_fit_table_311.py` covers the rule, the tier lookup, the fallback rows and the row parsing.
- The existing picker tests pin the new defaults.
