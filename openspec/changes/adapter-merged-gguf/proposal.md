# A trained adapter is merged into its base and registered as a GGUF

Full spec: `docs/specs/adapter-merged-gguf.md`. Issue: #273. Lane: `pillar:model`. Protected paths: `anthill/training/**`.

## Why

Ollama 0.34.1 and later refuse `ollama create ... ADAPTER`; the app's own adapter folder layout already fails on the shipped
0.30.10, and Qwen bases never worked with `ADAPTER`. Merging into the base and converting to GGUF works on both engines and
keeps the memory-safe GGUF build.

## What changes

The training image merges the adapter and converts it with a pinned llama.cpp converter (`merged.gguf` beside the adapter);
`register_with_ollama` registers that file with the base model's own template on any Ollama; the run is no longer stopped
before it starts; the eval gate is untouched.

## Guardrails (do NOT touch)

The eval gate and the rejection rule, the Apple Silicon local path, the engine pin, the pull path, the catalogue.
