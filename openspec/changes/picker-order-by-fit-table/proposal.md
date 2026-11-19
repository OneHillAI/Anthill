# The picker's default is the largest model the catalogue marks "fits" for the memory tier

Full spec: `docs/specs/picker-order-by-fit-table.md`. Issue: #311. Lane: `pillar:model`.

## Why

With every score cleared (#304) the old ranking falls back to the memory ceiling, which depends on a speed estimate and
can pick a model the catalogue itself marks `tight` or oversized. Ordering should be one lookup of precomputed data.

## What changes

`sizing.py` reads the row's `fit` and picks the largest `fits` model for the memory tier (Apple, one stream, 8k window);
other cases and rows without a verdict keep the current rule.

## Guardrails (do NOT touch)

- No change to the trust checks, the signing workflow or the generator; no new score or speed estimate.
