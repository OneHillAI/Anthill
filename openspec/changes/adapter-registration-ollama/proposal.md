# A trained adapter that Ollama will not register is reported before it is trained

Full spec: `docs/specs/adapter-registration-ollama.md`. Issue: #273. Lane: `pillar:model`.

## Why

Ollama removed `ollama create ... ADAPTER` in v0.34.1. Anthill still registers every non-MLX fine-tune that way, after
training it, and showed the refusal as a generic failure. The call also looked for `ollama` on `PATH`, where the bundled
engine is not.

## What changes

- A version probe of the server `ollama create` will use; the executor stops a non-MLX run with a plain note before any
  training compute is spent when that Ollama refuses adapters.
- `register_with_ollama` names the refusal, finds the engine the way the app does, and reports a missing engine plainly.
- Tests for each; no change to the eval gate, the Apple Silicon path or any serving setting.

## Guardrails (do NOT touch)

- The Apple Silicon local training path and its mlx-lm server; the eval gate.
- No training tool is installed and no model pulled. The replacement route stays open until a real adapter can be tested.
