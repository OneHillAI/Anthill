# Model catalogue release 1: backfilled rows, explicit GGUF tags, read licences, scores unscored

Full spec: `docs/specs/model-catalogue-release-1.md`. Issue: #304. Lane: `pillar:model`.

## Why

The 43 scores have no source and no independent index matches them; 13 rows use a plain tag that Ollama 0.40.x serves as
an MLX build with unbounded memory growth; rows carry no licence terms, region or memory fit, and three licence labels
were wrong.

## What changes

One data change to `anthill/hosting/model_catalog.json` plus its test: scores become 0 (unscored), additive keys carry
the licence, region, validation state and a precomputed memory fit, the 12 explicit GGUF tags are stored in `gguf_tag`
(`ollama_tag` unchanged), and the wrong licence labels are corrected. No code path changes.

## Guardrails (do NOT touch)

- No change to `is_safe_ollama_tag`, `is_safe_row`, `load_catalog`, the generator, the signing workflow or any gate.
- Old keys keep their meaning, so an old app reads the file unchanged.
- No Artificial Analysis number is used. No download or install.
