# Spec: Local model download progress and desktop pull resume

Status: implemented. Lane: `pillar:model`.
Relates to: `anthill/web/app.py`, `anthill/desktop.py`, `anthill/web/templates/dashboard.html`, `anthill/web/templates/models.html`, `anthill/web/templates/personalize.html`.

## 1. Problem

When a user selects a local model during first-run setup or from the model settings pages, `_start_model_pull()` spawns `ollama pull <tag>` in a background thread with `stdout=subprocess.DEVNULL` and `stderr=subprocess.DEVNULL`. While the pull runs (often several gigabytes over minutes), the UI only knows the model tag being pulled (`_MODEL_PULLING[org_id]`), with no visibility into percentage complete, downloaded/total bytes, transfer speed, or estimated time remaining.

Additionally:
- The Settings -> Model tab (`/personalize?tab=model`) did not display an active download banner at all.
- When the desktop app was closed or restarted while a chosen local model download was still in progress, `cfg.local_model_chosen` was already `True`, so the first-run wizard no longer appeared, yet `_ensure_engine_async()` did not resume the unfinished `ollama pull` on launch.

## 2. Requirements

1. **Live pull progress parsing:** `_start_model_pull()` must stream `ollama pull` output (which emits carriage-return updates with ANSI escape codes), strip ANSI sequences, and parse percentage, downloaded/total size, transfer speed, ETA, and phase into an in-memory per-org progress map (`_MODEL_PULL_PROGRESS[org_id]`), cleared automatically when the pull finishes or fails.
2. **Status endpoint exposure:** Both `GET /local-model/status` and `GET /models/pull-status` must include a `"progress"` field containing the latest parsed progress dictionary (or `None` when idle).
3. **UI progress bars:**
   - The Dashboard local-model banner (`anthill/web/templates/dashboard.html`) must render a live progress bar and metadata line (`%`, downloaded/total size, speed, ETA) while a model is downloading.
   - The Models page pull banner (`anthill/web/templates/models.html`) must render the same live progress bar and metadata line.
   - The Settings -> Model tab (`anthill/web/templates/personalize.html`) must include a pull banner and progress bar polled via `/models/pull-status`.
4. **Desktop pull resume on launch:** In `anthill/desktop.py`, once `ensure_serving()` succeeds in `_ensure_engine_async()`, if the single-org user has already chosen a local model (`local_model_chosen=True`, `solo_compute="local"`) and that model is not yet installed in Ollama, the desktop entrypoint must automatically start or resume the background pull via `_start_model_pull(org_id, model_tag, activate_when_done=False)`.

## 3. Implementation

- `anthill/web/app.py`:
  - Added `_MODEL_PULL_PROGRESS: dict[int, dict[str, object]]` and `_parse_ollama_pull_line(line: str) -> dict[str, object] | None`.
  - Updated `_set_model_pulling()` to clear `_MODEL_PULL_PROGRESS` when the pull state clears.
  - Updated `_start_model_pull()` to read carriage-return/newline chunks from `subprocess.Popen(..., stdout=subprocess.PIPE, stderr=subprocess.STDOUT)` and update `_MODEL_PULL_PROGRESS[org_id]`.
  - Added `"progress": _MODEL_PULL_PROGRESS.get(org.id)` to `/local-model/status` and `/models/pull-status`, and passed `"pulling"` to `personalize.html`.
- `anthill/desktop.py`:
  - Added a post-engine startup check in `_ensure_engine_async()` that inspects the single org's `OrgSettings` and resumes `_start_model_pull` when a chosen local model is missing from Ollama.
- `anthill/web/templates/dashboard.html`, `anthill/web/templates/models.html`, `anthill/web/templates/personalize.html`:
  - Added progress bar elements and polling logic to render live percentage, byte counts, speed, and ETA.
- `tests/test_local_model_settings.py`:
  - Added unit tests for `_parse_ollama_pull_line` and for `"progress"` reporting and cleanup on `/models/pull-status` and `/local-model/status`.

## 4. Acceptance Criteria

- [x] `_parse_ollama_pull_line` extracts percentage, downloaded/total bytes, transfer speed, and ETA from `ollama pull` progress output (including ANSI-escaped lines) and returns phase updates for non-percentage lines.
- [x] `GET /models/pull-status` and `GET /local-model/status` expose `"progress"` while a pull is active and reset `"progress"` to `None` when the pull finishes.
- [x] Dashboard, `/models`, and Settings -> Model (`/personalize`) render a live progress bar with percentage, downloaded/total size, speed, and ETA during local model downloads.
- [x] Restarting the desktop app when a chosen local model download is incomplete automatically resumes the background pull once the local engine is serving.
