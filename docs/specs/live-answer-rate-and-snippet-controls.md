# Spec: give a just-streamed answer real rate/snippet controls instead of "reload to rate / save"

Status: implemented
Lane: `pillar:model`
Relates to: `docs/specs/manual-escalation-always-offered.md` and `docs/specs/ask-provider-now.md` (the
two places a live answer's controls get attached), `docs/specs/cancel-local-generation.md` (the
sibling live-chat-session follow-up that closed question #3 of the same four).

## 1. Introduction

Question #4 of four raised in a live chat session: what is the half-a-button labelled "reload to rate
/ save", sitting next to "save as task" under a freshly-streamed answer?

Reading `addAssistantControls` (the function that attaches controls to a just-streamed answer, called
from `runStream`'s `_finalize` and from `fireAskProviderNow`'s success handler) found it was never
actually inert - its own `reloadBtn.onclick = () => location.reload();` genuinely reloaded the page.
The bug was one level up: a real, persisted message id is already available by the time either call
site runs (`chat_stream`'s SSE emits `{'meta': {'message_id': ...}}` well before `[DONE]`, and
`/chat/{id}/ask-provider-now`'s JSON response carries `message_id` too), but `addAssistantControls`
never received it, so it could not attach the real thumbs-up/down or snippet handlers that a reloaded
history answer gets (`rate()`, `saveSnippet()` - both id-keyed) - and reaching for "just reload the
page, then those work" was the only thing left for it to do. A "half a button" that already worked
exactly as coded was reported as a bug because what it did (reload) was a poor substitute for what a
human expects a "rate this answer" control to do (rate this answer, immediately).

## 2. Requirements

### R1 - A live answer gets the same controls a reloaded one has, without reloading
- WHEN a just-streamed (or just-escalated-via-ask-provider-now) answer's real message id is already
  known to the client, THE SYSTEM SHALL attach thumbs-up, thumbs-down, save-as-snippet, and
  save-as-task controls wired to that id - the same four controls, in the same order, a server-rendered
  history answer already has.
- THE SYSTEM SHALL NOT require a page reload for any of R1's controls to work.

### R2 - No id, no fake controls
- WHERE no real message id was ever sent for a turn (the `looks_like_remember` quick-ack path, and a
  `plane_unavailable` error both save a message but never emit a `message_id` meta), THE SYSTEM SHALL
  offer only "save as task" (which already tolerates a null source id) - not thumbs/snippet buttons
  that would silently no-op, and not the retired reload placeholder either.

## 3. Design

- **`chat.html`'s `addAssistantControls(container, promptText, answerText, msgId)`**: gained a fourth
  parameter. When `msgId` is truthy, renders the four-button set and wires `rate(msgId, ±1)`,
  `saveSnippet(bubbleText, msgId)`, and `scheduleCardAfter(..., sourceMessageId: msgId)` - identical
  wiring to the server-rendered history markup (`chat.html`'s Jinja loop), so `rate()`'s
  `.msg[data-id="..."] .thumbs` index lookup (button 0 = up, 1 = down) lines up the same way. When
  `msgId` is falsy, renders only the save-as-task button (R2).
- **Call site 1 (`runStream`'s `_finalize`)**: already tracks `assistantMessageId` from the SSE
  `meta.message_id` event in the same closure - now passed straight through instead of being read and
  then dropped.
- **Call site 2 (`fireAskProviderNow`'s success handler)**: the JSON response's `message_id` was
  already being sent by the server and already being ignored client-side; now also sets the answer's
  `.msg` `data-id` attribute (mirroring the SSE path) before passing it through, since `rate()` and
  `saveSnippet()` both resolve their target via that attribute, not the parameter alone.
- **Snippet text**: reads `container.querySelector('.bubble').innerText` at click time rather than the
  raw streamed markdown buffer, matching what the history path's snippet button already sends
  (rendered plain text, not markdown source).

## 4. Tasks

- [x] `addAssistantControls` takes `msgId`; real controls when present, save-as-task-only fallback when
  not (R1, R2).
- [x] Both call sites thread their already-available message id through.
- [x] `fireAskProviderNow` sets `data-id` on the answer's `.msg` (previously never set for this path).
- [x] Browser tests: a live answer's controls carry a real thumbs-up round trip through to the database
  and open the snippet modal with the right content; a live answer with no id offers only save-as-task,
  with neither rate/snippet buttons nor the retired reload text.
- [x] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **Historical answers** (server-rendered from `messages`) - already correct, unchanged.
- **`renderEscalationOffer`'s merge-in-place success path** (`/chat/{id}/escalate-confirm`) - it
  appends to an answer bubble that was already finalized (with real controls) earlier in the same turn;
  nothing new to wire there.
