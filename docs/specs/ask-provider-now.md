# Spec: offer the attached provider from the start of generation, not just at the end

Status: implemented
Lane: `pillar:model`
Relates to: `docs/specs/manual-escalation-always-offered.md` (#824, the post-hoc always-offer this
extends earlier in the turn), `docs/specs/escalation-model-drift.md` (#821), `docs/specs/expert-tier-
compound-compute-and-escalation.md`.

## 1. Introduction

Founder feedback, live: a question to a small local model (qwen3.5:9b) took 30+ seconds, and only once
it finished was the user offered the attached provider ("This needs more than your local model") -
#824's always-offer, working as designed. The complaint: waiting through the entire slow local answer
just to be offered the fast alternative afterward defeats the point of having one. Direction given
directly: build the "always offer, human decides" principle (#824) one step earlier - available from
the moment generation starts, not gated behind local finishing - and after 15 seconds of still waiting,
visually highlight it next to the response so it is not missed once patience is more likely to be
running out. The highlight is pure elapsed-time styling, not a difficulty judgement - it carries none of
the unreliability QA already found in trying to auto-detect "this needs an expert" (#820).

## 2. Requirements

- THE SYSTEM SHALL offer "Ask {Provider} instead" from the moment local generation starts (not after it
  finishes) whenever an escalation provider is attached, for a plain chat turn (not agent/research
  mode, and not an already-shown `confirm` proposal - tool-calling and multi-step research don't map
  onto "just ask the provider instead" cleanly).
- THE SYSTEM SHALL visually emphasise the offer after 15 seconds of continued local generation, using
  a fixed elapsed-time threshold only - never a judgement about the question's difficulty (the failure
  mode #820 already ruled out for auto-triggering).
- THE SYSTEM SHALL apply the SAME consent contract as every other escalation path: a not-yet-consented
  account sees the existing 3-choice first-use card before anything leaves the device; an already-
  consented account fires in one tap. Asking early changes nothing about who must approve what.
- THE SYSTEM SHALL respect the existing monthly escalation cap and audit logging - this is still a
  third-party disclosure, not a free pass around either gate.
- WHERE local generation is still running when the provider answer is chosen instead, THE SYSTEM SHALL
  NOT block on, wait for, or silently discard local's eventual answer - it may complete and save its own
  message later, independent of what the user already saw. (See "known limitation" below - cleanly
  cancelling local mid-generation is explicitly out of scope for this change.)

## 3. Design

### Backend

- **`chat_conv`'s template context** (`anthill/web/app.py`) gains `escalation_provider_label` (the
  attached provider's display name, or `""` when none is configured) and `escalation_consented`,
  exposed to `chat.html` as `ESCALATION_PROVIDER_LABEL` / `ESCALATION_CONSENTED` JS globals - the
  client needs to know both up front to decide whether to show the offer at all and which of the two
  shapes (3-choice card vs. one-tap) it should be.
- **`POST /chat/{conv_id}/ask-provider-now`** (new): unlike `/chat/{id}/escalate-confirm`, which
  appends to an ALREADY-SAVED local answer, there is no local answer yet here - local is still
  generating server-side when this fires. Looks up the conversation's most recent user message (saved
  by `chat_stream` before local generation begins, so it already exists), runs the same
  cap-check / `_build_attachment_backend` / `.chat()` / audit-log sequence `escalate-confirm` already
  uses, and saves the result as its OWN new assistant `ChatMessage` (`escalated=True`,
  `answered_locally=False`) rather than appending to anything.

### Frontend (`chat.html`)

- `renderAskProviderNow(bubble, message)`: appends the offer as a **sibling** of the streaming bubble
  (inside `bubble.parentElement`), not inside the bubble's own `innerHTML` - the bubble's content is
  replaced wholesale on every throttled token redraw while streaming, which would otherwise silently
  destroy the offer element and any listener bound to it every ~60ms. A `setTimeout(…, 15000)` adds a
  `.highlighted` class; `id`-free (styled via a stable class instead), so this needs no coordination
  with the redraw loop at all.
- Not consented: clicking swaps the offer's own content for the same 3-choice shape (Always / Just
  this once / Not now) `renderEscalationOffer` already uses elsewhere, reusing its visual language
  (`--warn`/`.badge-escalated` tokens) rather than inventing a new one.
- On fire: abandons the in-flight `EventSource` (`currentSrc.close()`) and discards `_finalize` without
  calling it - the provider's answer is a replacement for local's, not an appendix to it. Reuses
  `setEscalating(true, label)` for the same "Checking with {Provider}…" status text #812/#824 already
  render elsewhere.
- Cleanup: the offer element is removed once local finishes on its own (`_finalize`) or the turn
  becomes a make/do/schedule proposal instead of a plain answer (the `d.proposal` SSE branch) - in
  either case the offer is moot once there is nothing left to ask instead of.

## 4. Known limitation (explicitly out of scope here)

Local generation cannot be cleanly cancelled mid-request in the current architecture - an Ollama call
blocks until it returns, and nothing in `chat_stream` currently checks for early client disconnection.
Closing the `EventSource` client-side stops the CLIENT from rendering local's tokens, but the SERVER
keeps generating and will still save its own assistant message when it eventually finishes, independent
of the provider-answered message the user already saw. In the common case (the provider answers in
low single-digit seconds; local was already 15s+ in when the user clicked) the user has moved on well
before local's answer lands, and the extra message is easy to ignore; a user who reloads the
conversation while local is still finishing could see the same question answered twice, a short
distance apart. Solving this properly needs request-cancellation support in `chat_stream` itself - real,
separate work, not attempted in this change.

## 5. Tasks

- [x] `chat_conv` context: `escalation_provider_label`, `escalation_consented`.
- [x] `POST /chat/{conv_id}/ask-provider-now`: cap check, attachment backend, own assistant message,
  consent persistence, audit log.
- [x] `chat.html`: offer rendered as a bubble sibling from generation start; 15s highlight; 3-choice /
  one-tap consent shapes; abandon-and-replace on fire; cleanup on normal completion or a proposal turn.
- [x] Tests: the new endpoint's own message creation, cap enforcement, missing-attachment and
  missing-question error paths, a provider failure surfacing cleanly with nothing saved, and the
  page's JS globals reflecting the attached provider's label and consent state.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 6. Out of scope

- **Cancelling local generation server-side** - the known limitation above; needs its own design.
- **Agent/research-mode turns** - "ask the provider instead" doesn't map cleanly onto tool-calling or
  a multi-step research pass; the offer is not shown for either.
- **Racing local and the provider concurrently, or any other timing strategy** - the founder's explicit
  direction was always-offer-from-the-start + a 15s highlight, not a race or a smarter heuristic.
