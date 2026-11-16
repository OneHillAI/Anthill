# Spec: Settings model card shows the model's own size

Status: implemented. Lane: `pillar:model`. Issue: #160.

## Problem

Settings > Where your AI runs > Your machine showed `qwen2.5:3b` with "On this device - 4.6 GB - fully offline".
The model is 1.9 GB (first-run setup and `ollama list` agree). The page summed every file under the Ollama
models folder, which also holds other models (here `bge-m3`) and leftover blobs. On a small SSD the difference
matters, and the card read as the size of the model.

## Requirements

1. The card shows the size of the configured model (`ollama_model`), taken from Ollama's `/api/tags` through
   `OllamaBackend.installed_models_with_sizes()`. Tags are matched with `:latest` treated as implicit, the same
   rule the rest of the app uses.
2. If Ollama cannot be reached, or the configured model is not installed, no size is shown. The card never falls
   back to the folder total.
3. The folder total is still useful for disk planning, so it appears on its own line, "Models on disk: X GB in
   total", only when the model's own size is known and the folder is larger. It is never shown in place of the model's size, and not at all when the size is unknown.
4. Nothing changes for a Solo workspace running on its private cloud GPU: the card shows no local size and no
   folder line.

## Known limits

- The folder total counts everything under `OLLAMA_MODELS` (or `~/.ollama/models`), including partial downloads
  and blobs no installed model uses. It is labelled as the folder, not as anything Anthill can reclaim.
- Walking the folder happens on each Settings load. It was already done before this change.

## Acceptance

- With `qwen2.5:3b` at 1.9 GB and `bge-m3` at 1.2 GB installed and a 4.6 GB models folder, the card reads
  "On this device - 1.9 GB - fully offline" and a second line reads "Models on disk: 4.6 GB in total".
- A configured `llama3` matches an installed `llama3:latest`.
- With Ollama unreachable or the model not installed, the card reads "On this device - fully offline".
- With a folder total equal to the model's size, there is no "Models on disk" line.
- Tests: `tests/test_settings_model_size.py` (model-free).
