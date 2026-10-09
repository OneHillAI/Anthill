# Solo chat: web search defaults off

Status: **superseded on 2026-10-08** by the founder's decision that web search is on by default for new accounts (see
`docs/specs/chat-thinking-toggle.md` and the note in `docs/specs/solo-web-search-default-off.md`).

Full spec: `docs/specs/solo-web-search-default-off.md`.

## Why

A hands-on QA pass found that Solo chat's "Running on this machine. Nothing leaves it." banner was
contradicted by its own Web search toggle defaulting on - checking it sends the query text to DuckDuckGo
(or Google Custom Search) by default. Verified still true on `main` before starting this fix.

## What changes

- `anthill/web/templates/chat.html`: the `opt-web` checkbox's default `checked` attribute is now
  conditional on `active_conv.plane != 'solo'` (Solo starts unchecked; Org unaffected).
- The Solo trust banner (`#chat-trust-msg`) is kept in sync with the live toggle state via a small JS
  listener, instead of rendering a static, unconditionally-true claim.
- No changes to `anthill/agent/intent.py`'s `decide_web()` or `anthill/web/app.py`'s `auto_web` -
  both already gate/flag correctly; this is a default + messaging fix only.

## Guardrails (do NOT touch)

- `auto_web`'s transparent "🌐 web" per-message badge stays as-is.
- Org's web-search default stays on (org already runs on a shared, non-local model).
