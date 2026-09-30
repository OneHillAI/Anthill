# Tasks

- [x] Add `stays_local()` to `anthill/inference/base.py`.
- [x] Wire outbound scrub + inbound restore (streaming-safe) into `OpenAICompatBackend.chat`/
      `chat_stream`/`chat_with_tools`; skip scrubbing for loopback endpoints.
- [x] Add `anthill.web.audit.log_inference_call()`; call it at all 6 `plane_inference()` call sites
      (`app.py` chat + `_org_plane_answer`, `agents_run.py` x2, `scheduler.py` x2).
- [x] Add `ChatMessage.answered_locally`; set at both assistant-message save sites; emit as SSE meta
      event; render badge in `chat.html` (server-rendered history + live JS stream).
- [x] Tests: `tests/test_openai_compat.py` (scrub/restore/loopback-skip/streaming-split-placeholder),
      `tests/test_inference_call_audit.py` (event content, never-raises).
- [x] Update `docs/specs/model-onboarding-and-sovereignty.md` R6 table cell + new R9; new
      `docs/specs/661-egress-privacy-and-audit.md`.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy` (clean set), full `pytest`.
- [ ] Live-verify in the browser: send a message containing an email address through a connected
      remote endpoint (or a mocked one), confirm the outbound payload is redacted (network tab) and
      the rendered answer restores the value; confirm the local/remote badge renders correctly for a
      local (Ollama) turn and a remote (org endpoint) turn.
- [ ] Add changelog.d fragment once a PR number exists.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR with the
      `pillar:privacy` label + `## Disclosure` checkbox + `## Spec` section referencing
      `docs/specs/661-egress-privacy-and-audit.md`. Monitor check-runs + `asdd/review` to green; merge
      only on explicit "merge #N" instruction.
