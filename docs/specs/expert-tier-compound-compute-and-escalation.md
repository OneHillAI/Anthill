# Spec: expert tier - compound compute + escalation attachment

Status: implemented. Lane: `pillar:model`.
Relates to: `docs/specs/278-local-escalation-engine.md` (the base engine this builds on - Ask mode reuses
it unchanged; Automated mode is new), `docs/specs/model-onboarding-and-sovereignty.md` (the compute
chooser this restructures), `docs/specs/661-egress-privacy-and-audit.md` (the audit-log plumbing every
escalation call gets for free).

## 1. Problem

Solo accounts pick ONE of three mutually-exclusive compute tiers today: Your machine (local, full
ownership), Your cloud (self-provisioned RunPod/Lambda, full ownership), or Inference provider (a hosted
API, partial ownership, no local model at all). Picking the inference provider tier means EVERY turn
runs on a third party - there is no way to keep a fully-owned lead model for everyday use while still
reaching for a stronger hosted model specifically when the lead's own answer is weak. #278 already
built the underlying mechanism for exactly this ("escalate this turn to a stronger connected backend"),
but only for Chat's manual redo phrase (Ask mode), a human-approved Agent proposal, and a Task's fixed
creation-time setting - never for Chat automatically, and never through an attachment orthogonal to the
base compute choice.

Founder: "local or cloud, plus an inference provider next to it, combines into the expert one" - the
inference-provider tier should stop being a third competing choice and become something EITHER
fully-owned tier can optionally attach, specifically for hard questions.

## 2. Requirements

- THE SYSTEM SHALL replace the three-way compute chooser (local / cloud / provider) with two base tiers
  (local / cloud), each able to optionally attach ONE inference provider (Berget/Groq/Infercom) as an
  escalation path - never as the sole/primary backend.
- THE SYSTEM SHALL keep the attachment orthogonal to which base tier is active: attaching a provider
  SHALL NOT change which models form the account's council (#278's existing "council and escalation
  stay mutually exclusive" rule), and SHALL NOT touch the base tier's own primary-backend fields
  (`org_provider`/`org_model_endpoint` for local's implicit state, or the cloud tier's own
  `org_provider`/`org_provision_key_enc`).
- THE SYSTEM SHALL offer two escalation modes, chosen once at attachment time: Ask (the existing #278
  redo-phrase flow, enhanced with a free confidence signal folded into its uncertainty check) and
  Automated (fires without a follow-up, notifies after the fact, never blocking).
- THE SYSTEM SHALL bound Automated mode with a monthly call-count cap
  (`OrgSettings.escalation_cap_per_month`, default 20) - the same call-count-not-dollar-cap reasoning as
  the existing Task escalation cap, since no reliable per-call cost figure exists for an arbitrary
  attached provider. Ask mode needs no cap: a human retyping the redo phrase is the rate limit.
- THE SYSTEM SHALL make the escalation decision using a real confidence signal when the backend provides
  one (mean per-token log-probability), before ever spending a paid escalation call - Ask mode ORs it
  with the existing text-hedge check at no extra cost; Automated mode uses it as a fast-path (skip
  outright when confident, escalate outright when clearly not) and falls back to ONE local grader call
  only in the borderline band or when no confidence signal is available.
- THE SYSTEM SHALL NOT let an already-manually-escalated turn (Ask mode's `escalate_org`) also trigger
  Automated mode, and SHALL NOT re-grade a cache hit against a fresh attachment call.
- THE SYSTEM SHALL NOT let an escalation failure retroactively mark an already-successfully-shown local
  answer as failed - an escalation attempt is strictly additive once the local answer exists.
- THE SYSTEM SHALL give Chat a distinct visual state for an escalated turn (a third badge state, a
  distinct avatar accent, and a "working" indicator that visibly changes while the escalation call is in
  flight) - never a blocking UI, never a silent one.

## 3. Design

### Backend

- **`InferenceBackend.chat_with_confidence()`** (`anthill/inference/base.py`): additive to the Protocol,
  `chat() -> str` is completely unchanged (no changes at its ~20 existing call sites). Returns a new
  `ChatResult(text, confidence: float | None)` - `confidence` is the mean per-token logprob when the
  backend/model/response actually provided one (Ollama's native `/api/chat` logprobs support, any
  OpenAI-compatible endpoint's `logprobs`/`top_logprobs`), `None` on anything else (older Ollama, a
  provider without support like Groq, any parsing failure) - never a guess, never treated as "confident"
  by default.
- **Trigger logic** (`anthill/agent/intent.py` + `anthill/web/escalation.py`), two independent functions
  sharing the same threshold constants (`CONFIDENCE_UNCERTAIN`/`CONFIDENCE_CONFIDENT` - explicitly
  flagged as uncalibrated starting points, not measurements, matching this codebase's existing honesty
  convention for similar placeholder constants):
  - `intent.seems_uncertain_for_ask(answer, confidence)`: OR of the existing `looks_uncertain()` text
    hedge check and a low-confidence signal - free, no extra call, since `chat_with_confidence()` already
    ran on the same turn.
  - `escalation.should_escalate_automated(local_backend, question, result)`: confident skips outright,
    clearly-uncertain escalates outright, the borderline band (or `confidence is None`) spends
    `grade_answer_locally()`'s one extra local call - defaults to escalating on any ambiguous grader
    reply or backend error (a spurious escalation costs one paid call; a false negative here ships a
    wrong answer unattended, the worse failure).
- **`OrgSettings` schema** (`anthill/web/db.py`): six new columns - `escalation_provider`/
  `escalation_provider_key_enc` (which provider, if any, and its encrypted key), `escalation_mode`
  (`ask`/`automated`), and the cap trio `escalation_cap_per_month`/`escalations_this_month`/
  `escalations_reset_at` (mirrors `task_escalation_cap_per_month`'s shape exactly). Deliberately NOT
  reusing `cloud_provider` (the older, unrelated hybrid-cloud-fallback feature).
- **`_apply_solo_compute`** (`anthill/web/app.py`): the standalone `compute == "provider"` branch is
  removed entirely (its capability - a hosted endpoint with no local model, a multi-model council sharing
  one endpoint+key - is retired, not migrated: the new attachment never touches council membership).
  `_apply_escalation_attachment()` (shared by the `"cloud"` branch and the implicit `"local"` path)
  validates and writes the three attachment fields; a blank key reuses the already-saved one for the
  SAME provider, switching providers always needs a fresh key. Existing accounts already configured the
  old way (`org_provider` set to an inference-provider key, no local model) are unaffected - this only
  changes what a NEW save through this chooser can produce, not how already-saved config is read at the
  routing layer.
- **Chat wiring** (`anthill/web/app.py`'s `chat_stream`): Automated mode's escalation runs as a
  FOLLOW-UP phase, strictly after the lead's own answer has already fully streamed - never blocking or
  delaying the first answer. On trigger: a `{meta:{escalating:true}}` SSE event, one call to
  `_build_attachment_backend()` (resolves the attachment into a one-off backend using each provider's
  curated flagship model - the attach UI has no model picker of its own), the escalated text appended to
  the SAME assistant message, then `{meta:{escalated:true}}`. Gated on: automated mode configured, not
  already manually escalated this turn, not a cache hit, monthly cap not reached. Own try/except -
  mirrors `scheduler._maybe_escalate_task`'s "never raises" contract, since a failure here must never
  retroactively fail an already-shown answer.
- **Known scope boundary**: the trigger always runs with `confidence=None` for Chat specifically (falls
  through to the local grader call every time), since `wiki.ask.ask()`/`ask_stream()` - which sit in
  front of caching, wiki retrieval, and web search - don't yet expose the logprob signal a single direct
  `chat_with_confidence()` call can provide. Threading confidence through their shared contract (used by
  Agents/Tasks too) is real, separate follow-up work, not a shortcut taken here; the trigger is still
  fully correct with `confidence=None`, just never the "free" fast path for Chat yet.

### Frontend

- **`_compute_chooser.html`**: two base cards (`data-tier="local"`/`"cloud"`), each with its own
  "Connect an inference provider for hard questions" checkbox that reveals a second, independent
  provider-connect section (separate from the cloud tier's own required RunPod/Lambda picker) plus an
  Ask/Automated mode toggle. Chip labels: Graduate (local) / Professional (cloud) base, Expert once
  attached - not three tiers competing for a "top" label.
- **`_council_builder.html`**: `ccCouncilTier()` simplified to the two-way tier; an attached escalation
  provider never changes which models form the council.
- **`chat.html`**: a third meta-badge state + distinct avatar accent for `msg.escalated`/
  `d.meta.escalated`, and a caped-ant "working" indicator swap via `setEscalating()` while
  `d.meta.escalating` is set - the in-bubble dot-trail indicator for normal generation is unchanged.

## 4. Tasks

- [x] `InferenceBackend.chat_with_confidence()` + `ChatResult` + per-backend logprob fold-in
      (Ollama, OpenAICompatBackend, MlxBackend's trivial wrapper).
- [x] Two-mode trigger logic (`intent.seems_uncertain_for_ask`, `escalation.should_escalate_automated`/
      `grade_answer_locally`) + tests.
- [x] `OrgSettings` schema (six new columns, verified auto-migrating).
- [x] `_apply_solo_compute` restructuring: retire the standalone provider tier, add
      `_apply_escalation_attachment` to the local/cloud branches, update both route call sites
      (`/setup/model`, `/personalize/compute`) + their now-obsolete/adapted tests.
- [x] Wire Automated mode into Chat's SSE stream (`_build_attachment_backend`, the escalating/escalated
      meta events, cap bookkeeping) + tests.
- [x] Unit-level backend test pass (`_build_attachment_backend`, attachment save-path edge cases) - caught
      and fixed a real import bug (`from .config import Config` resolving to the nonexistent
      `anthill.web.config`) the higher-level SSE tests had mocked past.
- [x] Compute-chooser restructuring, council-builder simplification, Chat badge/indicator states
      (frontend half, same branch).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2458 passed,
      10 skipped, 0 regressions).

## 5. Out of scope

- Full self-consistency/semantic-entropy uncertainty estimation - the confidence signal is a single
  mean-logprob figure plus a hedge-phrase check, not an ensemble method.
- A dollar-denominated budget for Automated mode - a call-count cap is the answer for now, matching
  Task escalation's existing precedent.
- Threading `chat_with_confidence()`'s logprob signal through `wiki.ask.ask()`/`ask_stream()` - Chat's
  trigger works correctly today via the local-grader-call fallback; exploiting the free fast path there
  is separate follow-up work (see the Design section's "known scope boundary").
- Merging Council and escalation - explicitly kept mutually exclusive, per #278's existing rule.
- Any change to Advanced/BYO-cloud (manually connecting your own endpoint) - untouched.
- A vision-turn escalation path - out of scope, matching #278's existing image-turn exclusion.
- Any new blocking/confirm-first UI - both modes stay non-blocking by design.
