# Tasks

- [x] Decision 0: narrow `plane_inference`'s ONE-MODEL-PER-ACCOUNT branch to `deployment_topology ==
      "org"`; `normalize_topology` docstring fix; test coverage proving a solo-topology account with a
      connected endpoint stays local by default.
- [x] Chat: `intent.redo_mode()` gains a `"provider"` value; the pre-existing `prior`-gate bug for
      "web"/"deep" fixed alongside it; early `escalate_org` detection before plane resolution;
      `provider_available` threaded through `wiki/ask.py`'s suggestion text.
- [x] Agents: `_propose_escalation()` (post-hoc, approval-gated) + `_execute_escalation()` +
      `execute_approved()` dispatch branch.
- [x] Tasks: `ScheduledTask.escalate_on_uncertainty` + `OrgSettings` monthly-cap columns;
      `_maybe_escalate_task()`; `_verify_task_result()` OR-combine fix; Tasks page UI (conditionally
      rendered checkbox + admin cap field).
- [x] Shared `anthill/web/escalation.py`.
- [x] Tests: `tests/test_plane_routing.py`, `tests/test_intent_depth.py`,
      `tests/test_ask_confidence_suggestion.py`, `tests/test_agents_surface.py`,
      `tests/test_task_escalation.py`, `tests/test_task_escalation_settings.py`.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2093 passed).
- [x] Live-verify in the browser: confirmed a solo-topology account with a connected (fake) endpoint
      stays on local by default (no false "org model unreachable" banner - caught and fixed a real
      pre-existing regression this exposed, see below); "use the cloud model" as a follow-up correctly
      attempted the connected endpoint (not Ollama - proven by the returned "can't reach
      gpu.fake-endpoint.example" error naming that exact host); the Tasks page checkbox + admin monthly-
      cap field both render correctly once a backend is connected.
- [x] **Real regression caught during live verification**: `chat.html`'s "Solo borrows the org model,
      here's why it fell back to local" banner was driven by the OLD broad `is_org_mode` check, not the
      new narrower `shares_org_model` - so it falsely fired for a solo-topology account with a merely-
      connected (not shared) endpoint. Renamed the template context key to `shares_org_model` and fixed
      its value at the source (`app.py`'s `chat_conv` route); audited every other `is_org_mode` call
      site and confirmed they're all legitimately broad (org wiki/nav visibility, principle/skill
      scoping, plane-availability guards) and correctly unaffected by Decision 0.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR with the
      `pillar:model` label + `## Disclosure` checkbox + `## Spec` section referencing
      `docs/specs/278-local-escalation-engine.md`. Monitor check-runs + `asdd/review` to green; merge
      only on explicit "merge #N" instruction.
