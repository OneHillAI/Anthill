# Spec: explicit "Redo with provider" and "Export" buttons on chat answers

Status: implemented. Lane: `pillar:feature`.
Relates to: `docs/specs/chat-escalation-visibility-fixes.md` (same live-QA round found this),
`docs/specs/manual-escalation-always-offered.md` (#820's consent-dance pattern, reused here).

## Problem

#421 retired the per-answer "redo with a bigger model" and "export as PDF" buttons in favor of two
things: the router silently deciding answer depth, and natural-language re-asks ("go deeper", "check
the web", "give me that as a PDF") routed through the agent's intent layer. Live testing this round
found both language routes unreliable for the two specific asks a user actually made:

- Asking to redo a specific past answer "via groq" was read as a reference to the unrelated GROQ
  query language, not an instruction to escalate that answer to the connected `groq` provider.
- Asking for "the previous answer as a PDF" produced a generic copy-paste non-answer instead of
  triggering `create_file`.

Both backends the buttons would need already exist and work: `/chat/{id}/escalate-confirm` (resolves
via the specific assistant message's own paired question, not just "the latest message" - already
correct for redoing an arbitrary past turn) and `/chat/download` (kept alive after #421 specifically
"for programmatic use").

## Fix

Two explicit, icon-only buttons added to the existing per-answer control row (👍 👎 ✂️ 📅), so a
answer now carries up to six icons without wrapping:

- **Export (⬇️)**: click reveals an inline row of format choices (PDF / Word / Excel / Markdown,
  reusing the existing `fmtLabel` map) in place of the button - the same reveal-in-place pattern the
  file uses elsewhere, not a dropdown. Posts to `/chat/download` with the bubble's own markdown
  (`bubble.dataset.md`, already hydrated for both a live-streamed and a page-reloaded message by the
  existing `renderHistory()` pass) and opens the resulting file URL in a new tab.
  `/chat/download`'s format allow-list widened from `{pdf, docx, md, txt, html}` to add `xlsx` -
  `create()`'s `_xlsx` already parses a markdown table or CSV/TSV out of arbitrary content, which a
  lot of real chat answers (comparisons, breakdowns) already are.
- **Redo with {provider} (⚡)**: only rendered when an escalation provider is attached
  (`ESCALATION_PROVIDER_LABEL` client-side / `escalation_provider_label` in the Jinja reload context)
  and the answer hasn't already been escalated. Reuses the exact Always/Just-once/Not-now consent
  dance `askProviderNowClicked` already shows for the automatic offer, then posts to
  `/chat/{id}/escalate-confirm` with this specific `message_id` - so it correctly redoes any past
  answer in the conversation, not only the one just streamed.

Both buttons are wired in the two places a chat answer's controls are built: the live-JS
`addAssistantControls()` (a just-streamed answer) and the separate, independently-maintained Jinja
block in `chat.html`'s message loop (a page-reloaded conversation) - these two paths render the same
row from duplicated markup and have drifted before, so both needed the same change.

The existing "save as task" button's text label was dropped (icon + title only, matching the other
four) to keep the row compact now that it carries six controls instead of four.

## Verification

`tests/test_chat_download.py::test_download_accepts_xlsx_for_a_table_answer` covers the new format.
The two buttons' wiring has no dedicated Python test (client-side JS, no JS test runner in this
stack, consistent with prior chat.html-only changes this cycle) - verified instead by live browser
testing against a running `anthill web` instance: Export correctly produces a downloadable `.xlsx`
from a table-containing answer (confirmed via the `/chat/download` response and the follow-up file
GET), and Redo correctly shows the consent row, posts to `/chat/1/escalate-confirm`, and surfaces a
graceful "inference provider is not reachable" state (rather than a stuck indicator) when the
attached provider's key doesn't resolve - exercised on both the page-reload and live-streamed render
paths, confirming they stayed in sync. Full targeted suite (`chat`/`escalat`/`download`/`provider`
keyword match, 362 passed) and `ruff check`/`ruff format --check` pass; `anthill.web.app` is
grandfathered out of the mypy-blocking module list (pre-existing SQLAlchemy `Column` false-positives).
