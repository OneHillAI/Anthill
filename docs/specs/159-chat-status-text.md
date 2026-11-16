# Spec: chat status text matches the real state

Status: implemented. Lane: `pillar:model`. Issue: #159.

## Problem

Three small messages in the chat contradicted what the page showed elsewhere, and made a first-time user wonder
whether something was set up wrong (#159):

- While an answer was being written, the bubble said "Thinking, locally." even with Thinking off ("Thinking off" or
  "No thinking mode" in the composer).
- A banner said the local model is "too small, so web search uses your question as is" while web search was off.
- After the first message the header still read "New conversation", although the server had already stored the
  title and the sidebar showed it later.

## Requirements

1. The line a local turn starts with is "Thinking, locally." only when the Thinking button is on and the model has a
   thinking mode. Otherwise it is "Writing, locally.". One helper (`workingText`) chooses it for both places that
   start a working row.
2. The "too small" note is rendered only for a model that cannot plan, as before, but starts hidden while web
   search is off for the chat, and is shown and hidden with the Web search button (`syncTrustBanner`), including
   when a new default is applied from the first-use notice.
3. When a message gives a new conversation its title (first message, title still "New conversation"), the stream
   sends it first as `{"meta": {"title": ...}}` and the page sets the header with `textContent`. A chat that already
   has a title, and a confirmed proposal, send no title event.

## Known limits

- The title is the first 60 characters of the first message, as before. A better title is a separate change.
- The sidebar entry is not updated live; it shows the title on the next page load. Only the header follows.
- "Writing, locally." is also shown for an answer from the attached inference provider until that turn reports
  "Checking with ..."; the line names where the work starts, as it did before.
- The page's JavaScript is tested as template text and, in `tests/browser/`, in a real browser (not run here
  without Playwright). The server side is tested model-free.

## Acceptance

- With a small local model and web search off, the page renders the note with `hidden`; with web search on it does
  not.
- Toggling Web search shows and hides the note without a reload.
- With thinking off, or on a model with no thinking mode, a new turn's line reads "Writing, locally."; with thinking
  on and a model that thinks it reads "Thinking, locally.".
- The first message of a new chat sends a title event, and the header changes to it.
- Tests: `tests/test_chat_status_text.py` (model-free) and `tests/browser/test_chat_web_stage_browser.py`.
