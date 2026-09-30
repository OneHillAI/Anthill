# Spec: egress PII scrubbing, per-turn audit trail, and a user-visible local/remote indicator (#661)

Status: implemented. Lane: `pillar:privacy`.
Relates to: `docs/specs/model-onboarding-and-sovereignty.md` (R9, this change's summary in the sovereignty
table), `docs/specs/chat-depth-autoroute.md` (the existing hedging/"go deeper" UX pattern this reuses).

## 1. Problem

Once an account connects a real inference provider (Berget) or its own RunPod/onprem cloud, every chat,
task, and agent turn on that plane sends the full raw wiki context, conversation history, and question to
`anthill.inference.openai_compat.OpenAICompatBackend`, which POSTs it unredacted. The PII scrubber that
exists in this codebase (`anthill/hybrid/scrub.py`) was built for a different, now-retired feature (the
consent-gated third-party vendor escalation path in `anthill/hybrid/escalate.py`) and was never wired to
this call site - the one that actually carries real org/provider traffic today.

Separately, nothing records which model or endpoint answered a given turn (the existing `AuditLog` table
covers admin/security events and per-tool-call agent actions, but not inference calls), and nothing tells
the user, in the chat itself, whether a given answer stayed on their device or left it.

## 2. Requirements

- THE SYSTEM SHALL scrub structured PII (email, phone, SSN, payment cards, API keys/JWTs, IBANs, and,
  when the optional Presidio pack is installed, free-text names/locations/government IDs) from every
  message before `OpenAICompatBackend` sends it to a remote endpoint, and SHALL restore the original
  values in the returned content (including mid-stream, without ever emitting a broken/partial
  placeholder) so the user sees natural text, not `[EMAIL_1]`-style tokens.
- THE SYSTEM SHALL NOT scrub calls whose endpoint is loopback (`localhost`/`127.0.0.1`/`::1` - the
  on-device mlx-lm fine-tune server): nothing crosses a perimeter there, so scrubbing would only cost
  latency for no privacy benefit.
- THE SYSTEM SHALL NOT restore placeholders inside tool-call arguments (only the message `content`) -
  the model only ever saw scrubbed text, so a PII-shaped value in an argument is itself a placeholder,
  not a real leak, and restoring inside arbitrary tool-call JSON risks corrupting a real argument value.
- THE SYSTEM SHALL record an `inference.call` audit event (existing `AuditLog` table, via
  `anthill.web.audit.log_inference_call`) for every turn resolved through `plane_inference()` - chat,
  task runs, agent runs, agent-approval continuations, the Slack/Discord/widget member-interaction
  path, and skill auto-learning distillation - capturing which surface, plane, backend, model, and
  endpoint answered it, attributed to the acting org/user. THE SYSTEM SHALL NOT record message content
  in this event - that is the scrubber's job, not the audit trail's.
- THE SYSTEM SHALL NOT let an audit-logging failure break the run it instruments (best-effort, matching
  the existing `agent.tool`/`agent.blocked` pattern).
- THE SYSTEM SHALL show the user, per successful assistant message, whether that turn was answered locally
  or left the device - both in the live SSE stream (a `meta.answered_locally` event) and in the persisted
  chat history (`ChatMessage.answered_locally`) - not only logged for later admin review. Failed-generation
  badge filtering is owned by [`757-mlx-chat-streaming.md`](757-mlx-chat-streaming.md).

## 3. Design

- **Scrub site.** `anthill/inference/openai_compat.py` is the single real network-egress point for every
  OpenAI-compatible call (org/RunPod/Berget), used by Chat, Tasks, and Agents alike - scrubbing here once
  covers all three surfaces, rather than at every call site that builds a prompt. A new
  `anthill.inference.base.stays_local(backend, base_url)` helper (reused by both the scrub gate and the
  UI indicator) decides whether a given call crosses a perimeter at all.
- **Streaming-safe restore.** `_restore_stream()` buffers back to the last unmatched `[` until it closes,
  so a placeholder split across two SSE token chunks is never shown broken mid-stream.
- **Audit call sites.** `log_inference_call()` is called once at each of the 6 places `plane_inference()`
  is invoked (`app.py`'s chat route and `_org_plane_answer`, `agents_run.py`'s `run_agent` and
  `execute_approved`, `scheduler.py`'s `_run_task` and `_distil_skill_from_agent`), not inside
  `plane_inference()` itself - that module is deliberately a pure, side-effect-free "testable seam"
  (its own docstring), and adding a DB write there would break that.
- **UI indicator.** `ChatMessage.answered_locally` (new additive Boolean column, migrated automatically by
  the existing generic `ensure_columns()` pass) is set from the same `stays_local()` call at the point
  each assistant message is saved, and successful responses render a small badge (💻 local / ☁️ remote)
  next to the existing cache-hit/wiki-citation badges - both in server-rendered history and in the
  live-streamed JS bubble, matching the existing `cache_hit`/`wiki_slugs`/`auto_web` meta-event pattern.

## 4. Tasks

- [x] Add `stays_local()` to `anthill/inference/base.py`.
- [x] Wire outbound scrub + inbound restore (streaming-safe) into `OpenAICompatBackend.chat`/
      `chat_stream`/`chat_with_tools`.
- [x] Add `audit.log_inference_call()` and call it at all 6 `plane_inference()` call sites.
- [x] Add `ChatMessage.answered_locally`; set it at both assistant-message save sites; emit it as an SSE
      meta event; render the badge in `chat.html` (server-rendered + live JS).
- [x] Tests: scrub/restore/loopback-skip/streaming-split-placeholder (`tests/test_openai_compat.py`),
      audit event content + never-raises (`tests/test_inference_call_audit.py`).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest`.

## 4a. Known issue fixed after initial ship (2026-08-04)

A hands-on QA audit found `_scrub_messages` merged each message's independently-numbered placeholders
(`scrub()` starts its `[KIND_N]` counter at 1 on every call) into one shared restore map - two different
messages sharing a PII kind (e.g. an email in the wiki-context message and a different email in the
question, a routine grounded turn) both produced `"[EMAIL_1]"`, so the later message's mapping silently
clobbered the earlier one, and `restore()` could put the wrong person's PII into the reply. Fixed by
suffixing each message's placeholders with its position in the batch before merging (`"[EMAIL_1]"` ->
`"[EMAIL_1_m0]"`), keeping them unique across the outbound call. See
`tests/test_openai_compat.py::test_two_messages_with_the_same_pii_kind_do_not_clobber_each_others_mapping`.

Separately flagged in the same audit, now also fixed (see 4b below): `Message.images` was never passed
through `_scrub_messages`, so an image attached to a chat turn reached a remote/org backend with zero
redaction.

## 4b. Known issue fixed after initial ship (2026-08-04): images bypassed scrubbing entirely

Unlike text, there is no honest way to "scrub" arbitrary pixel content with the regex/Presidio mechanism
above, so this is a fail-closed gate rather than an attempted redaction: `_scrub_messages` strips
`images` from any message crossing a non-local perimeter (`_scrub_egress == True`) and appends a short
note to the message text explaining why, so the model's reply doesn't silently pretend the image was
seen. The on-device/loopback vision path is completely unaffected - `stays_local()` skips this function
entirely, so a locally-served vision model (Ollama) still receives images exactly as before. See
`tests/test_openai_compat.py::test_remote_backend_strips_images_and_notes_why` and
`::test_loopback_endpoint_keeps_images_and_skips_scrubbing`.

## 5. Out of scope

- A live, per-turn "escalate this turn from local to the inference provider" decision engine (Chat's
  visible opt-in suggestion, Agent's approval-gated escalation, Task's fixed-at-creation choice) - a
  real, separate design discussed alongside this change but not built here; this change is the
  plumbing (know which model answered, keep the data clean) that design would sit on top of.
- Free-text name/location detection beyond the optional Presidio pack (already gated behind the
  existing `privacy` extra, unchanged by this work).
- Restoring PII inside tool-call arguments (explicit non-requirement above).
