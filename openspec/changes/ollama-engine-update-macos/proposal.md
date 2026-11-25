# Update the bundled Ollama to 0.40.16 on macOS

Full spec: `docs/specs/ollama-engine-update-macos.md`. Issue: #264. Lane: `pillar:model`. Protected paths: `anthill/inference/**`, `scripts/**`.

## Why

The maintainer decided on 2026-11-23 to move the macOS engine from 0.30.10 to 0.40.16 (Windows stays). Measured on the same model files: decode
8 to 14% faster, prefill the same within noise, memory the same; cached tokens are reported. The exact GGUF tags (#345) are on main.

## What changes

The macOS pin (`_OLLAMA_VERSION`, `_OLLAMA_DARWIN_SHA256`, `scripts/build-app.sh`), a separate Windows pin, the catalogue engine check reading the
platform's own pin, recorded 0.40.16 fixtures, the pin tests.

## Guardrails (do NOT touch)

The Windows engine, the catalogue rows, the training path, any release step.
