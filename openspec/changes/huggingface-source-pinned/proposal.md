# Hugging Face as a second model source, with the file fingerprint checked after the pull

Full spec: `docs/specs/huggingface-source-pinned.md`. Issue: #292. Lane: `pillar:model`. Changes a foundation.

## Why

The catalogue can list only Ollama library tags; the tag gate rejects `hf.co` references on purpose. Most regional and
specialised models are on Hugging Face. The maintainer decided on 2026-11-19 to add it as a second source with
safeguards. A measurement (Ollama 0.40.16) showed a fingerprint cannot be pinned inside the pull reference, so the
safeguard is a check after the pull.

## What changes

A written proposal only, no production code: a typed catalogue source, a verify-then-admit pull flow, the behaviour on a
mismatch, a threat note, what breaks, and what is still unmeasured.

## Guardrails (do NOT touch)

- No change to `is_safe_ollama_tag`, `is_safe_row`, any gate or any pull path in this change.
- No download or install without the maintainer's go; the measurement used a scratch engine and a throwaway store.
- Which models are in the catalogue is decided in the separate catalogue task.
