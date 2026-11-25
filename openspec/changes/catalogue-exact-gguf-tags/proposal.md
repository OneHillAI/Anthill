# The ambiguous catalogue rows name the exact GGUF build, old plain tags kept working

Full spec: `docs/specs/catalogue-exact-gguf-tags.md`. Issue: #345. Lane: `pillar:model`.

## Why

On Ollama 0.40.x a plain tag pulls the MLX build, whose memory grows without limit on a 16 GB Mac; the exact GGUF tag stays flat.
The 13 rows whose plain tag is ambiguous must name the exact build before the engine moves.

## What changes

A data change for 13 rows (`ollama_tag` becomes the exact tag, `replaces` keeps the plain tag), plus the small page fix the switch
needs so an install under the old tag stays selected and uninstallable in the Settings picker.

## Guardrails (do NOT touch)

Engine pin, the pull path, sizing, `need_gb`, rows not listed. No download beyond the handshake already done.
