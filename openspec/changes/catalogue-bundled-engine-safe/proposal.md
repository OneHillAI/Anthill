# The catalogue offers only models the bundled Ollama can download

Full spec: `docs/specs/catalogue-bundled-engine-safe.md`. Issue: #318. Lane: `pillar:model`. Replaces PR #310.

## Why

The shipped engine (Ollama 0.30.10) cannot download four catalogue models, and the first-run suggestion for 16 and 24 GB
Macs points at one of them. A model the shipped engine cannot run must not appear.

## What changes

Data only: the four rows (`qwen3.8:27b` and the three `nemotron-3.5` models) are removed from the catalogue file, and
tests check that none is offered or recommended at any memory tier. No new fields, no picker, default or score change,
no engine update.

## Guardrails (do NOT touch)

- `anthill/hosting/sizing.py` logic, defaults, scores, council behaviour and the pull error path.
- The engine pin. No download or install without the maintainer's go.
