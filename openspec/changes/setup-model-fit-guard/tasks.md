# Tasks

- [x] `model_picker_post`: re-check `choice` with `_model_too_large_for_this_machine(cfg, choice)` before
  accepting it; on a too-large choice, redirect to `model_picker.html` with a clear error instead of
  saving/pulling it.
- [x] Route `model_picker_post` and `_maybe_autopull_vision` through `_installed_local_models(cfg)`
  (already exception-safe) instead of a raw `OllamaBackend(...).installed_models()` call; broadened
  `OllamaBackend.installed_models`/`resident_models` themselves to also catch `ValueError` (a non-JSON
  response), matching `context_window`'s existing "never raises" guarantee.
- [x] `model_picker.html`: add an `error == 'too_large'` banner (same copy/pattern as `models.html`/
  `personalize.html`).
- [x] Tests: submitting a catalog-known, too-large `choice` to `/setup/model` is rejected (no pull
  started, `ollama_model`/`local_model_chosen` unchanged) and shows the error; an already-installed tag
  is unaffected; a malformed (non-JSON) Ollama response during `/setup/model` and during
  `_maybe_autopull_vision` no longer 500s; `OllamaBackend.installed_models`/`resident_models` survive a
  non-JSON response directly.
- [x] Full local validation (`ruff`, `mypy`, `pytest` - 2320 passed) + live browser verification (a
  direct POST of `gpt-oss:120b` is refused with the error banner and no download; a fitting model
  choice is still saved correctly end to end).
