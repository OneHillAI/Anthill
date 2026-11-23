# A memory need for each catalogue model up to 10 GB, by the calibrated rule

Full spec: `docs/specs/catalogue-need-up-to-10gb.md`. Issue: #336. Lane: `pillar:model`.

## Why

The app reads `need_gb` per row (#320) but no row carries one, so the flat formula decides; it is 2.1 to 2.6 GB too strict for
small models. The maintainer decided on 2026-11-20 to use `1.3 x file size + 1.2 GB` up to a 10 GB file and keep the formula
above it.

## What changes

A data change in `anthill/hosting/model_catalog.json`: `need_gb` and `need_basis` on 13 rows. Four fit labels change (at 8 and
16 GB) and the 8 GB default and first-run suggestion move from Qwen3.5 2B to Nemotron 3 Nano 4B; the spec lists them.

## Guardrails (do NOT touch)

Sizing code and thresholds, the engine pin, the pull path, tags, scores, rows above 10 GB. No download.
