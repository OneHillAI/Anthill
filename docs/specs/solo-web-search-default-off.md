# Solo chat: web search defaults off

## Problem

The Solo chat header shows **"Running on this machine. Nothing leaves it."** while the Web search
toggle defaulted **on** (`chat.html`, `<input id="opt-web" checked>`). Turning web search on genuinely
sends the query text off-machine to a third-party search provider (`anthill/search/web.py`: DuckDuckGo by
default, or Google Custom Search if keys are set) - a real egress path, not a cosmetic toggle. For a
privacy-first product, the single most prominent claim on the chat screen was contradicted by its own
default behavior. Confirmed still present on `main` via a hands-on QA pass (2026-07-19) and re-verified in
code before this fix.

## Requirements

1. A **new Solo conversation** defaults Web search **off**. An **Org** conversation is unaffected (org
   data already runs on a shared, non-local model - the banner never claimed "nothing leaves it" there).
2. The Solo trust banner text stays accurate to the **live** toggle state (the checkbox can change without
   a reload, and the choice persists per-browser via `localStorage`): "Nothing leaves it." only while web
   search is off for this chat; otherwise "Web search is on for this chat."
3. No change to `decide_web()`'s gating (`anthill/agent/intent.py`) or the `auto_web` just-in-time
   override (`anthill/web/app.py`) - both already correctly gate on the toggle / transparently flag
   auto-enabled turns with a visible "🌐 web" badge. This fix is about the **default** and the **banner's
   honesty**, not the search-decision logic itself.

## Acceptance criteria

- A fresh Solo conversation's `/chat/{id}` page renders `<input type="checkbox" id="opt-web" >`
  (unchecked).
- A fresh Org conversation's page renders `<input type="checkbox" id="opt-web" checked>` (unchecked
  default is Solo-only).
- The Solo trust banner's server-rendered default text is "Running on this machine. Nothing leaves it."
- Toggling the checkbox updates the banner text immediately (client-side), without a page reload.
- No change to `tests/browser/test_chat_proposal.py`'s existing panel-visibility assertions.
