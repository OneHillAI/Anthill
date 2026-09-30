# Tasks

- [x] `InferenceBackend.chat_with_confidence()` + `ChatResult` (additive, `chat()` unchanged) + per-
  backend logprob fold-in for Ollama and `OpenAICompatBackend`; `MlxBackend`'s trivial wrapper.
  `tests/test_chat_confidence.py` (15 tests).
- [x] Two-mode trigger logic: `intent.seems_uncertain_for_ask()` (free, Ask mode) and
  `escalation.should_escalate_automated()`/`grade_answer_locally()` (logprob-first + one grader call,
  Automated mode), sharing `CONFIDENCE_UNCERTAIN`/`CONFIDENCE_CONFIDENT` threshold constants (explicitly
  flagged as uncalibrated starting points). `tests/test_escalation_trigger.py` (9 tests).
- [x] `OrgSettings` schema: `escalation_provider`/`escalation_provider_key_enc`/`escalation_mode` +
  the `escalation_cap_per_month`/`escalations_this_month`/`escalations_reset_at` cap trio - verified to
  auto-migrate against a deliberately incomplete `org_settings` table (no manual `_ensure_columns` entry
  needed).
- [x] `_apply_solo_compute` restructuring: remove the standalone `compute == "provider"` branch;
  `_apply_escalation_attachment()` shared by the `"cloud"` branch and the implicit `"local"` path
  (applied unconditionally before branching on council vs. single/no model, with its own commit so it
  persists even on the fallthrough "none" result); both `/setup/model` and `/personalize/compute` gain
  the three new form fields + a `bad_escalation_provider` redirect. Obsolete
  `compute == "provider"`-as-primary tests removed/replaced; new attachment save-path tests added
  (happy path, missing key, blank-key-reuses-saved-key, provider-switch-needs-fresh-key, clearing the
  attachment, works identically on the cloud tier).
- [x] Chat wiring: `_build_attachment_backend()` (resolves the attachment into a one-off backend using
  each provider's curated flagship model - verified against each provider's own current docs, not
  guessed) + the `chat_stream` follow-up phase (`escalating`/`escalated` SSE meta events, cap
  bookkeeping via `escalation_cap_reached`/`record_escalation_used`, own try/except so a failure never
  fails the already-shown local answer). `tests/test_chat_automated_escalation.py` (5 tests) +
  `tests/test_escalation_attachment_backend.py` (5 tests - caught and fixed a real
  `from .config import Config` import bug the SSE-level tests had mocked past).
- [x] Frontend: `_compute_chooser.html` restructured to two base cards + the attach toggle;
  `_council_builder.html`'s `ccCouncilTier()` simplified to two-way; `chat.html`'s escalated badge/
  avatar/indicator states.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2458 passed,
  10 skipped, 0 regressions).
