# Server-side re-check on model selection/pull

## Status

Fixed, in two passes. `fit_tier` (see `local-moe-offload-fit.md`, `hosting/sizing.py`) already computes
whether a catalog model runs on this machine, and `_model_picker_view`/`_local_model_options` already
use it to grey out or exclude too-large models in every picker UI (`personalize.html`,
`model_picker.html`, `models.html`). None of that reached the endpoints that actually act on a submitted
model tag - there were THREE such endpoints; the first pass fixed two of them and missed the third.

## Problem

`personalize_post` (`/personalize`), `pull_model` (`/models/pull`), and `model_picker_post`
(`POST /setup/model` - the tail end of first-run signup) all accepted whatever `ollama_model`/
`model_tag`/`choice` a request carried and saved/downloaded it without re-checking fit. The UI correctly
disabled a too-large option, but that is presentation only - a stale saved value (set before this
machine's fit was known, or before the fit filter existed), an option not fully disabled client-side, or
a direct request all reached the server unchecked. Concretely: a 120B-parameter model (e.g.
`gpt-oss:120b`) could be selected and downloaded on a 16GB Apple Silicon machine, which has no separate
VRAM to spill the rest to (unlike a discrete GPU box's CPU-offload path) - the result serves, if at all,
via macOS's virtual memory at a small fraction of a token per second, after an unnecessary tens-of-
gigabytes download.

The first pass (see git history) fixed `/personalize` and `/models/pull` but missed
`model_picker_post` - discovered after a report of a 500 while signing up. Investigation (device DB/
audit-log inspection, live reproduction of every reachable signup path, and a full read of the
setup/model-picker code) did not reproduce a crash directly from the missing fit check itself - a
too-large `choice` there was silently accepted and downloaded, not a 500 - but surfaced a SECOND,
related gap in the same route while looking for the crash: `model_picker_post` and
`_maybe_autopull_vision` both called `OllamaBackend(...).installed_models()` directly instead of the
existing `_installed_local_models(cfg)` helper (used everywhere else, e.g. `_local_model_options`,
`pull_model`) - the direct call only tolerated `httpx.HTTPError`, not a non-JSON response from whatever
is listening on the configured Ollama URL, which `resp.json()` raises as an uncaught `ValueError`
straight through the request handler. Both are fixed together since they sit in the same two functions.

## Requirements (EARS)

- WHEN `/models/pull` receives a `model_tag` that Anthill's catalog recognizes and `fit_tier` judges
  `too_large` for this machine, the system SHALL refuse the pull (no download, no model switch) and
  redirect with `error=too_large`, rather than trusting the client's request.
- WHEN `/personalize` receives an `ollama_model` that differs from the account's current selection and
  Anthill's catalog + `fit_tier` judge it `too_large`, the system SHALL refuse the save (leave the prior
  selection untouched) and redirect with `error=model_too_large`.
- WHEN `POST /setup/model` receives a `choice` that Anthill's catalog recognizes and `fit_tier` judges
  `too_large` for this machine, the system SHALL refuse it (no download, no `ollama_model`/
  `local_model_chosen` mutation) and redirect with `error=too_large`, completing the pattern across all
  three model-selection endpoints.
- WHEN any of these three endpoints probes Ollama for installed models synchronously on the request
  path, the system SHALL treat a non-JSON or otherwise unparseable response the same as unreachable
  (empty result), never propagate it as an unhandled exception - this includes the best-effort vision
  autopull (`_maybe_autopull_vision`), which runs synchronously on the same request before it hands off
  to its background thread.
- WHILE `ollama_model` is UNCHANGED from the account's current selection, the system SHALL NOT re-block
  it, even if it happens to be too large - the picker's own dropdown always includes the current
  selection, so an unrelated Settings save (a profile field, a toggle) must not be locked out by a
  pre-existing choice the user is not trying to change right now.
- IF a submitted tag is already installed locally, THEN the system SHALL NOT block it (already on disk;
  no new download/switch risk).
- IF a submitted tag is not present in Anthill's model catalog, THEN the system SHALL NOT block it (no
  `params_b` to judge it by - failing open on unknown data, not guessing).

## Acceptance criteria

- `POST /models/pull` with a catalog-known, too-large `model_tag` never calls `_start_model_pull` and
  never changes `OrgSettings.ollama_model`; the redirect carries `error=too_large`.
- `POST /personalize` with a catalog-known, too-large `ollama_model` different from the current value
  leaves `OrgSettings.ollama_model` unchanged; the redirect carries `error=model_too_large`.
- `POST /personalize` resubmitting the SAME too-large `ollama_model` already stored (e.g. an unrelated
  profile-field save) succeeds and saves the other fields normally.
- `POST /personalize` switching AWAY from a too-large model to one that fits succeeds.
- An already-installed tag, and a tag absent from the catalog (a manually-pulled or Hugging Face GGUF
  tag), are never blocked by any of the three endpoints.
- `POST /setup/model` with a catalog-known, too-large `choice` never calls `_start_model_pull` and
  leaves `OrgSettings.ollama_model`/`local_model_chosen` unchanged; the redirect carries `error=too_large`.
- `models.html`, `personalize.html`, and `model_picker.html` each render a clear, hardware-specific
  error message for the rejected case (reusing `hw_label`), not a bare redirect with no explanation.
- `OllamaBackend.installed_models`/`resident_models` never raise on a non-JSON response (mirrors
  `context_window`'s own existing "never raises" guarantee); `model_picker_post` and
  `_maybe_autopull_vision` use `_installed_local_models(cfg)` rather than a raw `OllamaBackend` call, so
  they inherit that same tolerance and never 500 the request on a malformed probe.

## Out of scope

- Retroactively resetting an account that already has a too-large model saved from before this guard
  existed - the fix stops it from happening again; recovering is the existing "pick a different model"
  flow, which now also refuses to let it happen a second time.
- OAuth self-registration's first-run-only gate (`oauth_login_outcome`) - a separate, deliberate
  security boundary, untouched here.
