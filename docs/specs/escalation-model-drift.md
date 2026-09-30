# Spec: escalation attachment survives a provider dropping the curated model

Status: implemented
Lane: `pillar:model`
Relates to: `#820` (Escalation attachment: shipped Berget `escalation_model` is stale -> handoff 404s),
`docs/specs/278-local-escalation-engine.md`.

## 1. Introduction

The expert-tier escalation ATTACHMENT (`_build_attachment_backend` in `anthill/web/app.py`, distinct
from the primary org/cloud plane) has no model picker of its own - it always requests each provider's
curated `escalation_model` from `_INFERENCE_PROVIDERS`. Berget removed the curated model
(`openai/gpt-oss-120b`, verified there 2026-08-07) from its catalog at some point before 2026-09-28.
Every automated-mode handoff to Berget since then has 404'd - **silently**: `chat_stream`'s escalation
block already streamed the lead's own (local) answer before attempting the handoff, and wraps the whole
follow-up in a bare `except Exception: pass` so a failure there can never retroactively mark that
already-successful answer as failed. The user just never received the expert answer they were promised,
with no error, no log line reaching them, nothing.

This spec covers the concrete fix (re-curate the stale id) plus closing the two gaps QA found alongside
it: a provider-side failure had no visible signal at all, and an escalation call had no output cap.

## 2. Requirements

### R1 - The curated Berget model is live
- THE SYSTEM SHALL use a `escalation_model` for Berget that exists in Berget's catalog at time of
  writing (`Qwen/Qwen3.8-27B-FP8`, confirmed end-to-end against a real handoff).
- Rationale: `openai/gpt-oss-120b` is confirmed removed from Berget; every call to it 404s.

### R2 - A provider-side call failure is visible, not silent
- WHEN the escalation attachment's `.chat()` call itself raises (a dropped model, a transient error,
  anything past a successfully-BUILT backend), THE SYSTEM SHALL emit a distinct SSE meta event
  (`escalation_failed`) the client can react to.
- THE SYSTEM SHALL NOT let this failure touch the lead's own answer, `full_response`, or the
  `escalated`/`_escalated` state - the existing "never retroactively fail an already-shown answer"
  invariant is preserved; only the silence is fixed, not the failure-isolation design.
- THE CLIENT SHALL, on receiving `escalation_failed`, stop showing "Checking with {Provider}..." and
  show a brief, honest replacement ("Couldn't reach {Provider}") instead of the text either hanging or
  just vanishing with the turn's normal end-of-stream cleanup.

### R3 - An escalation call has a bounded output length
- THE SYSTEM SHALL cap the escalation attachment's generated output (`max_tokens`) rather than
  requesting an unbounded completion.
- Rationale: measured against a live provider, an uncapped call to a large flagship model ran
  substantially longer (~19s) than the identical call capped (~1.4-3s), for a single-turn answer that
  has no business being open-ended.

## 3. Design

- **R1**: one-line data change in `_INFERENCE_PROVIDERS["berget"]["escalation_model"]`, comment updated
  with the re-curation date and rationale. Groq and Infercom's curated ids are left unchanged - QA
  flagged them as at the same *class* of risk (no live keys to re-verify with) but did not confirm
  either is actually stale, so this PR does not guess-change them (R2 below is the actual defence
  against this class of bug recurring, for all three providers).
- **R2**: the existing `try: escalated_text = attachment_backend.chat(...) ... except Exception: pass`
  in `chat_stream`'s automated-escalation block becomes a narrower `try/except` around only the
  `.chat()` call itself (not the whole block), so a failure there yields `{"meta":
  {"escalation_failed": true}}` instead of silently passing; success still yields `escalated: true` as
  before via an `else` clause. `chat.html`'s `onmessage` handler clears the caped-ant visual
  (`setEscalating(false)`) and shows a short status line on this event, mirroring the existing
  `escalating`/`escalated` handlers immediately above it. The manual `/chat/{id}/escalate-confirm`
  endpoint already returns a proper `502` JSON error on the same failure (chat.html already renders
  that inline) - only the automated streaming path was silent, so only it changes.
- **R3**: `Config` gets a new `max_tokens: int | None = None` field (default preserves today's
  unbounded behaviour for every other caller); `build_backend`/`OpenAICompatBackend` thread it into
  the `/chat/completions` payload only when set. `_build_attachment_backend` sets
  `config.max_tokens = _ESCALATION_MAX_TOKENS` (512) - the only call site that opts in.

## 4. Tasks

- [x] Re-curate Berget's `escalation_model` (R1).
- [x] Narrow the swallow to the `.chat()` call only; emit `escalation_failed` on failure (R2).
- [x] `chat.html`: handle `escalation_failed` (clear the caped state, show a short message) (R2).
- [x] `Config.max_tokens` threaded through `build_backend`/`OpenAICompatBackend`; escalation opts in
  at 512 (R3).
- [x] Tests: `_build_attachment_backend` caps output and no longer builds the known-stale Berget id;
  `OpenAICompatBackend` sends `max_tokens` only when configured; the automated-escalation SSE stream
  emits `escalation_failed` (not `escalated`) on a provider-side failure, without touching the
  persisted assistant message or the escalation cap bookkeeping.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **Re-verifying Groq's and Infercom's curated ids** - no live keys available to this change; left as
  a follow-up for whoever has them (tracked in #820's comments).
- **Runtime validation against a provider's `/models` before every escalation** - a live pre-flight
  check on every call adds latency to an already-slow path for a failure mode R2 already makes visible
  and non-silent; a periodic/at-attach-time check is a reasonable follow-up but not required to close
  the alpha-blocking bug.
- **The confidence-grading / self-grading-recall findings from the same QA pass** - real findings,
  filed in #820 as context, but a distinct problem from the 404 and not required to fix it.
