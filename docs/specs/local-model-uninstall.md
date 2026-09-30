# Spec: Uninstall a downloaded local model from the Local model page

Status: implemented. Lane: `pillar:model`. Issue: #415.

## Problem

The Local model page (`/models`) can pull/download models but has no way to **delete** one. A user who
accumulates several multi-GB models (e.g. `mistral-nemo:12b` ~7 GB + `qwen3:14b` ~9 GB) can't reclaim
disk or reduce memory pressure without dropping to a terminal (`ollama rm <tag>`). Founder-requested.

## Policy

- Each installed model in the "Installed on this device" table gets a **Remove** action (with a JS
  confirm that names the tag and its size on disk). It posts to `POST /models/delete`.
- **The currently-served model and the in-progress download cannot be removed** - both would leave the
  box with no working local model. Those rows show "in use" instead of a Remove button, and the route
  refuses them server-side (`error=in_use`), independent of the UI.
- Each row shows its **size on disk** (already available from Ollama `/api/tags`) and a **"resident"**
  badge when the model is loaded in memory now (from Ollama `/api/ps`) - so the user sees which removal
  also frees RAM.
- Deletion is via the Ollama backend (`OllamaBackend.delete_model` -> `DELETE /api/delete`); it is
  admin-only and audit-logged (`model.deleted`). A failure (engine down, tag missing) surfaces as
  `error=delete_failed` and changes nothing.

## Acceptance criteria

- `POST /models/delete` with a non-current tag calls the backend delete and redirects to
  `/models?deleted=<tag>`; the event is audit-logged.
- `POST /models/delete` for the current model or the downloading model is refused (`error=in_use`) and
  the backend delete is never called.
- A backend delete failure yields `error=delete_failed`.
- The `/models` table renders a Remove control + size for a removable model, "in use" for the current
  one, and a "resident" badge for a loaded model.
- `OllamaBackend.delete_model` issues `DELETE /api/delete {"name": tag}`; `resident_models` reads
  `/api/ps`. Both return safe defaults when the engine is unreachable.
- Covered by `tests/test_local_model_settings.py`.
