# Spec: always offer manual escalation, independent of the self-grader

Status: implemented
Lane: `pillar:model`
Relates to: `#820` (under-escalation recall finding), `docs/specs/expert-tier-compound-compute-and-
escalation.md` (the attachment + Ask/Automated modes this builds on), `docs/specs/escalation-model-
drift.md` (the adjacent #820 fix this ships alongside).

## 1. Introduction

QA's #820 follow-up measured Automated mode's escalation recall: hard questions escalated only 1/3 of
the time, because the trigger relies on the local model's own self-assessment (`should_escalate_
automated`'s grader, or a confidence signal) - and a small local model is routinely **confidently
wrong** on hard questions, which defeats any signal built from its own confidence by construction. QA
also tested mean-logprob confidence as a free alternative to the grader call and found the same failure
mode from a different angle: easy/hard confidence ranges overlapped (a hard question scored more
"confident" than an easy one in one measured case), so a better local signal cannot fix recall here.

Founder decision: stop trying to auto-detect "this needs an expert" and instead make the choice the
human's, every time. Ask mode's own trigger (the #278 redo-phrase, forcing the account's *primary*
org/cloud plane) was already a dead end for the escalation ATTACHMENT specifically - it only checks
`org_endpoint_connected`, never `cfg.escalation_provider` - so an attachment configured with
`escalation_mode="ask"` had no working way to reach it at all before this change, other than
Automated mode's own grader-gated consent offer.

## 2. Requirements

### R1 - A manual escalation offer appears after every eligible local answer
- WHEN an account has an `escalation_provider` attached, THE SYSTEM SHALL offer it after every turn's
  local answer that is not already escalated, cache-hit, or itself a manual `escalate_org` redo -
  regardless of `escalation_mode` (ask or automated) and regardless of whether Automated mode's own
  trigger ran, fired, or stayed silent because it was confident.
- THE SYSTEM SHALL NOT offer twice on the same turn: if Automated mode's own not-yet-consented offer
  already fired, this is the same offer, not an additional one.
- THE SYSTEM SHALL NOT offer once the monthly escalation cap is already reached, so the offer never
  invites a click that `/chat/{id}/escalate-confirm` would just reject.

### R2 - The offer's shape reflects prior consent
- WHERE the account has never consented (or last chose "just this once"), THE SYSTEM SHALL show the
  existing 3-choice first-use consent card (Always / Just this once / Not now) - unchanged from before
  this spec, since a not-yet-trusted attachment still needs an explicit decision about trusting it
  going forward, not just this one answer.
- WHERE the account already chose "Always", THE SYSTEM SHALL show a single one-tap "Check with
  {Provider}" button instead - the trust decision is already made; this is purely "do I want a second
  opinion on this specific answer."

### R3 - No new backend call path
- THE SYSTEM SHALL reuse the existing `/chat/{id}/escalate-confirm` endpoint unchanged for both shapes
  in R2 - it already resolves an arbitrary `message_id` against `_build_attachment_backend`, independent
  of how or why the offer was shown.

## 3. Design

- **`anthill/web/app.py`'s `chat_stream`**: a new sibling check runs after the existing Automated-mode
  try/except block (not nested inside it, so it applies to Ask mode and to a confident Automated
  verdict alike): if nothing has already set `_pending_escalation_offer` or `_escalated` this turn, an
  attachment is configured, and the cap isn't reached, it sets the offer with a `consented` field
  (`cfg.escalation_consented`) the client uses for R2. The existing not-yet-consented Automated offer
  now also sets `"consented": False` explicitly (previously implicit - that branch only ever ran when
  false), for a uniform payload shape.
- **`chat.html`'s `renderEscalationOffer`**: branches its rendered markup on `offer.consented` - the
  existing 3-button card verbatim when false, a single `.btn-doit` "Check with {Provider}" button
  (reusing the same CSS, no new styles) when true. Both wire the same click handler
  (`/chat/{id}/escalate-confirm`, same success/failure rendering) - only the initial markup and which
  buttons get listeners differs.
- Grader cost (#820's second finding item): left unchanged. QA measured it as sub-second and opt-in
  (Automated mode only); the founder did not separately ask for a change there, and the new
  always-available manual offer makes the grader's own recall even less load-bearing than before.

## 4. Tasks

- [x] New sibling offer check in `chat_stream`, gated on: no existing offer/escalation this turn, an
  attachment configured, cap not reached (R1).
- [x] `"consented"` field added to both offer construction sites (R2).
- [x] `chat.html`: two-shape rendering, shared click handling (R2, R3).
- [x] Tests: Ask mode gets an offer; a confident Automated verdict still gets an offer; the offer's
  shape matches consent state; no offer once capped; no duplicate offer when Automated mode already
  offered; no offer without an attachment configured.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **A better auto-detection signal** - QA's own findings rule this out as not fixable via the local
  model's self-assessment; not attempted here.
- **Touching the grader's per-turn cost** - QA recommended skip for alpha (sub-second, opt-in,
  even less relevant once escalation is user-driven); not a founder-directed change.
- **Retiring Ask mode's now-dead redo-phrase path for the attachment, or `escalation_mode` as a
  setting** - the mode still meaningfully controls whether Automated's *silent* auto-fire happens on
  top of the grader; the new offer is additive, not a replacement.
- **Showing the offer on historical (already-rendered, page-reload) messages** - scoped to the turn
  that just streamed, matching how the existing consent-offer already worked; extending it to chat
  history is a separate, larger UI change.
