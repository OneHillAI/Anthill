# Spec: `/chat/download` answered a raw 422 instead of its own friendly 400

Status: implemented. Lane: `pillar:feature`.

## Problem

Post-launch check on the public repo's CI/signing surface. `/chat/download`'s `content` field was
`str = Form(...)` - a required field. If a request omitted it, or posted it as an empty string,
FastAPI's own request validation rejected it with a bare `422 Unprocessable Entity` before the
handler ever ran - the route's own `text = (content or "").strip(); if not text: return 400` check
(with the friendly `{"error": "nothing to export"}` body) never had a chance to fire, since
`content=""` also satisfies "present but blank" and should have reached that check too.

Anthill's own JS caller (`chat.html`'s `fireExportAnswer`) always sends a non-empty `content`, so
this never surfaced in normal use - only a direct call to the route (or a future caller) would hit
it, but a public, documented internal endpoint answering an inconsistent error shape for the exact
input it already has code to handle gracefully is worth closing.

## Fix

`content: str = Form(...)` -> `content: str = Form("")`. A missing field now arrives as `""`, same
as an explicitly empty one, and both fall through to the existing `if not text: return
JSONResponse({"error": "nothing to export"}, status_code=400)` - no new branch, no new error shape.

## Verification

`tests/test_chat_download.py::test_download_with_no_content_field_is_a_friendly_400_not_a_422`
(new): posting with `content` omitted entirely returns `400` with the existing "nothing to export"
body, not `422`; `content=""` explicitly does too. The pre-existing whitespace-only case
(`test_download_rejects_bad_format_and_empty`) already covered "present but blank" and still passes
unmodified. Targeted suite (`chat`/`download` keyword match): 213 passed. `ruff check`/`ruff format
--check` and the em/en-dash slop gate both clean.
