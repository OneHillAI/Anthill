# First-run model picker: close the last unguarded model-selection endpoint

Full spec: `docs/specs/local-model-fit-server-side-guard.md` (extended by this change).

## Why

The founder reported a 500 while "signing up." Investigation (real device DB/audit-log inspection,
extensive live reproduction of every reachable signup path - solo, additional account, organization,
suggested model, manually-picked model - and a full read of the setup/model-picker code) did not
reproduce a crash directly, but surfaced a confirmed, real gap left over from #747:

`#747` (`fix(model-picker): re-check hardware fit server-side, not just in the UI`) added a server-side
`fit_tier` re-check to `personalize_post` (`/personalize`) and `pull_model` (`/models/pull`) - the two
endpoints known at the time to accept a submitted model tag with no fit validation. It missed a THIRD
one: `model_picker_post` (`POST /setup/model`), part of the first-run "Choose your AI" step (i.e. the
tail end of signing up). Its `choice` validation only checks

    valid = {m.ollama_tag for m in LOCAL_CATALOG} | installed_models
    if choice in valid: ...

`LOCAL_CATALOG` is every model with *some* self-serve path (local OR Anthill's own cloud tiers) - not
filtered by whether it fits *this* machine. A too-large tag (e.g. `gpt-oss:120b` on 16GB Apple Silicon)
still passes this check and gets saved + downloaded, exactly the bug #747 fixed elsewhere.

Separately, `model_picker_post` calls `_maybe_autopull_vision(org.id)` and (for the council-suggestion
branch) touches `OllamaBackend(...).installed_models()` synchronously, in the request-handling path, with
no try/except at the call site. `installed_models()` only catches `httpx.HTTPError`; a non-JSON `200`
response from whatever is listening on the configured Ollama URL raises an uncaught `json.JSONDecodeError`
that would surface as exactly the reported 500 - unreproduced here (this machine's real Ollama behaved
correctly throughout), but a real, unguarded exception path directly in the signup completion route.

## What changes

- `model_picker_post` (`/setup/model`): re-check a submitted `choice` against `fit_tier` for this
  machine before accepting it, using the same `_model_too_large_for_this_machine` helper #747 added -
  reject with a clear error instead of silently accepting/downloading, completing the pattern across all
  three model-selection endpoints (`/personalize`, `/models/pull`, `/setup/model`).
- Wrap the synchronous Ollama probes in `model_picker_post` and `_maybe_autopull_vision` so a malformed
  response from whatever is on the configured Ollama URL degrades to "treat as not installed" (matching
  `_installed_local_models`'s own existing fallback) instead of propagating as an unhandled 500 on the
  signup-completion request.
- `model_picker.html`: render the same hardware-specific error banner pattern `models.html`/
  `personalize.html` already have for `error=too_large`.

## Guardrails (do NOT touch)

- `_model_too_large_for_this_machine`'s existing fail-open behavior (unrecognized tag, already-installed
  tag) is reused as-is, not redefined.
- No change to `fit_tier`, `_model_picker_view`, or the GET-rendered picker's fit annotations (#744/#746/
  #748's work) - this is the POST-side guard only, exactly as #747 was for the other two endpoints.
- No change to the council-suggestion (`accept_suggestion`) branch's actual behavior beyond making its
  Ollama probe crash-safe - the 3-member council it builds is already capacity-checked by
  `suggest_local_setup` before this code runs.
