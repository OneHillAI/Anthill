# Expert tier: attach an inference provider to your machine or your cloud for hard questions

Full spec: `docs/specs/expert-tier-compound-compute-and-escalation.md`.

## Why

The compute chooser offers three mutually-exclusive tiers today: Your machine, Your cloud, or Inference
provider - picking the provider tier means every turn runs on a third party, with no way to keep a
fully-owned lead model for everyday use while still reaching for a stronger hosted model on hard
questions specifically. #278 already built the underlying escalation mechanism (redo a turn on a
stronger connected backend), but only reachable via Chat's manual redo phrase, a human-approved Agent
proposal, or a Task's fixed setting - never automatically in Chat, and never as an attachment orthogonal
to the base compute choice. Founder: "local or cloud, plus an inference provider next to it, combines
into the expert one."

## What changes

- `anthill/inference/base.py`: additive `ChatResult`/`chat_with_confidence()` on the `InferenceBackend`
  Protocol - `chat() -> str` is unchanged. Ollama and `OpenAICompatBackend` fold in a real mean-logprob
  confidence signal on the SAME call already being made; `MlxBackend` gets a trivial `confidence=None`
  wrapper (it's the local fine-tune eval-gate only, never live-serving).
- `anthill/agent/intent.py` + `anthill/web/escalation.py`: two trigger functions sharing threshold
  constants - `seems_uncertain_for_ask()` (free hedge-OR-confidence signal, Ask mode) and
  `should_escalate_automated()`/`grade_answer_locally()` (logprob-first, one local grader call when
  unavailable/borderline, before the paid call, Automated mode).
- `anthill/web/db.py`: six new `OrgSettings` columns - `escalation_provider`/
  `escalation_provider_key_enc`/`escalation_mode`, plus the `escalation_cap_per_month`/
  `escalations_this_month`/`escalations_reset_at` cap trio (mirrors the existing Task-escalation cap
  shape). Auto-migrates additively, no manual migration entry needed.
- `anthill/web/app.py`: `_apply_solo_compute` drops the standalone `compute == "provider"` tier entirely
  (its capability - a hosted endpoint with no local model, a multi-model council sharing one endpoint -
  is retired, not migrated). `_apply_escalation_attachment()` validates and applies the new attachment
  fields on the `"local"`/`"cloud"` branches. Chat's SSE stream (`chat_stream`) gains Automated mode's
  actual escalation: fires as a follow-up phase after the lead's own answer has already streamed, never
  blocking, bounded by the monthly cap, with its own try/except so a failure never retroactively fails
  an already-shown answer.
- `anthill/web/templates/_compute_chooser.html` + `_council_builder.html` + `chat.html`: two base cards
  (not three), each with an optional "attach a provider" toggle; the council builder stays two-way
  (attachment never changes council membership); Chat gets a third badge state and a caped-ant "working"
  indicator for an escalating/escalated turn.

## Guardrails (do NOT touch)

- No merging Council and escalation - stays mutually exclusive, per #278's existing rule.
- No change to Advanced/BYO-cloud (manually connecting your own endpoint).
- No vision-turn escalation - out of scope, matching #278's existing image-turn exclusion.
- No new blocking/confirm-first UI anywhere - both modes stay non-blocking by design.
- No dollar-denominated budget for Automated mode - a call-count cap only, matching Task escalation's
  existing precedent.
- No threading `chat_with_confidence()`'s signal through `wiki.ask.ask()`/`ask_stream()` in this change -
  Chat's trigger works correctly via the local-grader-call fallback today; exploiting the free fast path
  there is separate follow-up work.
