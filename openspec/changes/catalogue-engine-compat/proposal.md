# A catalogue row says which engine it needs; the local picker hides rows the bundled engine cannot run

Full spec: `docs/specs/catalogue-engine-compat.md`. Issue: #322. Lane: `pillar:model`. Stacked on #321.

## Why

Four rows the shipped engine could not download reached users (#318, removed by data in #319). Nothing in the app stops it
happening again: the catalogue is refreshed from outside and the engine an install runs is fixed by its release.

## What changes

An optional `min_engine_version` per row, compared with the engine the app ships (the pin, no network). A row that needs a
newer one is not offered in the local picker (first-run suggestion, family lists, the setup form's choices, the local model
options). No text about versions anywhere. No shipped row has the field, so no verdict changes.

## Guardrails (do NOT touch)

- Cloud and organisation sizing, installed models, the Advanced custom tag, the engine pin, the pull path, the fit
  calculation, the scores.
