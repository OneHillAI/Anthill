# Spec: local-to-provider escalation engine (#278)

Status: implemented. Lane: `pillar:model`.
Relates to: `docs/specs/chat-depth-autoroute.md` (the "system decides, no manual lever" principle this
reuses; Chat's redo-phrase mechanism this extends), `docs/specs/661-egress-privacy-and-audit.md` (the
PII-scrub + audit-log plumbing every escalation call gets for free), `docs/specs/model-onboarding-and-
sovereignty.md` (R1's `solo_compute`, the compute-tier choice this builds on).

## 1. Problem

Issue #278: "Detect when a local answer is weak and route the query to the org GPU backend
automatically." An account whose default is local (cost-saving, `solo_compute == "local"`) but that has
also connected an org/RunPod/inference-provider endpoint should be able to answer THIS turn on the
stronger backend when the local answer looks uncertain - without changing its default, and without a
silent, unlabeled auto-escalation (the founder's explicit, standing principle: never spend on / leave
the perimeter to a paid/external resource silently, but also never a blocking confirm-first dialog -
reuse the "system decides, labeled after the fact" pattern already shipped for depth, see
`chat-depth-autoroute.md`).

**Blocking finding, fixed first:** `plane_routing.plane_inference()`'s "ONE MODEL PER ACCOUNT" branch
routed *any* account with a validated endpoint to that endpoint for every Solo turn, regardless of
`solo_compute` - checked via `planes.is_org_mode(cfg)`, which is true the moment *any* backend was ever
validated, solo-topology included. This made the target scenario (a solo account with a connected-but-
not-default endpoint) unreachable: there was never a local answer to judge, since the account was
already always running on the connected endpoint. Narrowed to `_shares_org_model(cfg)` -
`is_org_mode(cfg)` AND `deployment_topology == "org"` - so a genuine multi-user org account is
unaffected (still always shares its one model), and a solo-topology account with a connected endpoint
stays on its local default, as `solo_compute` says.

## 2. Requirements

- THE SYSTEM SHALL let a solo-topology account with a connected org/RunPod/inference-provider endpoint
  stay on its local default (`solo_compute == "local"`) even after connecting that endpoint - the
  ONE-MODEL-PER-ACCOUNT sharing rule SHALL apply only to genuine multi-user org accounts
  (`deployment_topology == "org"`).
- THE SYSTEM SHALL let a Chat user, on a follow-up to an uncertain local answer, say a short natural-
  language phrase (e.g. "use the cloud model") to re-answer THIS turn on the connected backend - never
  persisted to the conversation's default plane or `solo_compute`. THE SYSTEM SHALL NOT offer this when
  no backend is connected, and SHALL reuse the existing redo-phrase mechanism (`intent.redo_mode`,
  extended with a `"provider"` value) rather than a new UI control.
- THE SYSTEM SHALL name the connected backend explicitly in the "go deeper" suggestion clause when one
  exists and this turn answered locally, so escalating is always a labeled, opt-in choice - never
  silent, never a blocking confirm-first dialog.
- THE SYSTEM SHALL, for a persistent Agent, propose (never silently perform) escalating a follow-up run
  when its finished answer looked uncertain and it answered locally with a connected endpoint available
  - recorded as a pending `AgentApproval` (the same governance gate already used for consequential tool
    actions), approved on the agent's page like any other pending action.
- THE SYSTEM SHALL make a Task's escalation choice ONCE, at creation/edit time
  (`escalate_on_uncertainty`) - never a live per-run decision, since no one is present to approve
  anything on an unattended task. THE SYSTEM SHALL bound Task escalation with an org-wide monthly
  call-count cap (`OrgSettings.task_escalation_cap_per_month`, default 20) - a call-count cap, not a
  dollar cap, since no reliable per-call cost figure exists for an arbitrary connected endpoint (onprem
  has none; a manually-connected inference provider isn't in the priced third-party vendor registry
  `anthill/hybrid/providers.py` owns).
- THE SYSTEM SHALL honestly flag (via `verify_needs_review`/`verify_reason`) a Task run whose answer
  looked uncertain but was NOT escalated (escalation off for this task, nothing connected, or the
  monthly cap reached) - never silently deliver a weak answer as if it were fine.
- THE SYSTEM SHALL NOT let a Task's own verifier judgment (`_verify_task_result`) and the escalation
  engine's flag clobber each other - both are real, independent signals; whichever ran first survives
  the other (OR-combined, not overwritten).
- THE SYSTEM SHALL scrub PII on every escalation call and record an `inference.call` audit event, for
  free, by routing every escalation through the existing `OpenAICompatBackend`/`log_inference_call`
  machinery (`docs/specs/661-egress-privacy-and-audit.md`) - no new privacy or audit plumbing needed.

## 3. Design

- **Decision 0 (routing fix).** `plane_routing._shares_org_model(cfg)` = `planes.is_org_mode(cfg)` AND
  `db.normalize_topology(cfg.deployment_topology) == "org"`. Used in place of the bare `is_org_mode`
  check in the ONE-MODEL-PER-ACCOUNT branch only - the Team-in-org branch keeps using `is_org_mode`
  unchanged (a different, correct use of "does this install have organizational infrastructure").
- **Chat.** `intent.redo_mode()` gains a `"provider"` value (a new `_REDO_PROVIDER` phrase set, same
  narrow/length-bounded discipline as the existing `_REDO_WEB`/`_REDO_DEEP`). The chat-stream route
  detects it *before* `effective_plane` is resolved (not in the later web/deep redo block, which runs
  too late to affect an already-resolved plane) and sets `escalate_org = True` for that turn only, when
  a follow-up exists and `plane_routing.org_endpoint_connected()` is true. `wiki.ask.ask()`/`ask_stream()`
  gain a `provider_available` parameter that swaps in a second suggestion clause
  (`_GO_DEEPER_SUGGESTION_WITH_PROVIDER`) naming the connected backend.
  - **Companion fix**: the pre-existing "web"/"deep" redo detection was gated on the `prior` query
    parameter, which the shipped Chat UI never populates (no call site sets it - the old redo buttons
    that used to populate it were retired in #421). Gated on `_has_prior_turn` (a real "does this thread
    have a prior assistant message" check) instead, making "go deeper"/"check the web" actually
    reachable from a normal typed follow-up for the first time.
- **Agents.** `agents_run._propose_escalation()` (mirrors `_approval_gate`'s exact pattern) runs after
  `run_agent()`'s answer is finalized (post council-review, since review may have resolved the
  uncertainty): eligible when the run answered locally, the answer looks uncertain, and an endpoint is
  connected. Records an `AgentApproval` (`tool="escalate_to_provider"`) and notifies the owner - never
  blocks or replaces the answer already returned for that run. `execute_approved()` gains an early
  branch dispatching to `_execute_escalation()`, which forces the org plane via the shared
  `escalation.build_escalation_backend()` and re-runs the goal as a tool-less one-shot (a re-answer, not
  a fresh tool-calling run).
- **Tasks.** `ScheduledTask.escalate_on_uncertainty` (new column) is set once via the Tasks page's
  create/edit form - the checkbox is rendered only when `org_endpoint_connected()` is true for the
  account, so nobody is offered a setting that can't do anything. `scheduler._maybe_escalate_task()` runs
  after `_run_task()`'s council review, checks eligibility + the monthly cap
  (`_reset_monthly_escalation_count_if_due()` lazily resets it, mirroring `cloud_spent_usd`'s running-
  total pattern), and either escalates (via the same shared `build_escalation_backend()`) or flags the
  task for review with an honest, specific reason. `_verify_task_result()` was changed to OR-combine its
  own verdict with whatever `_maybe_escalate_task()` already set, instead of unconditionally overwriting
  it.
- **Shared plumbing.** `anthill/web/escalation.py`'s `build_escalation_backend()` factors out "force-
  resolve the org plane, build a one-off backend, audit-log the call" - used by both Agents' and Tasks'
  execution paths. Each surface still owns its own goal/context/tool assembly (an Agent's escalation is
  a deliberately tool-less re-answer; a Task's could reuse its own tools in a future iteration) - that
  assembly is NOT shared, only the plane-forcing + backend-building boilerplate is.
- **A real regression Decision 0 exposed, caught during live verification.** `chat.html` had a client-
  side banner ("using local, org model unreachable") driven by the OLD broad `is_org_mode` check - it
  falsely fired for a solo-topology account with a merely-connected (not shared) endpoint, since that
  check was never narrowed alongside the backend routing fix. Renamed the template's context key to
  `shares_org_model` (`plane_routing.shares_org_model`, public - `org_endpoint_connected`'s sibling) and
  fixed its value at the source. Every other `is_org_mode` call site was audited and left unchanged -
  they're legitimately broad uses (org wiki/nav visibility, `agent_context_for`'s principle/skill
  scoping, plane-availability guards) unrelated to per-turn model routing.

## 4. Tasks

- [x] Decision 0: `_shares_org_model()`, the routing-branch change, `normalize_topology` docstring fix,
      new/updated `tests/test_plane_routing.py` coverage (including a route-level proof that a solo-
      topology account with a connected endpoint stays local by default).
- [x] Chat: `intent.py` `"provider"` redo mode + tests; the `prior`-gate companion fix; early
      `escalate_org` detection in `app.py`'s chat route; `provider_available` threaded through
      `wiki/ask.py`; route-level + `ask.py`-level tests.
- [x] Agents: `_propose_escalation()`, `_execute_escalation()`, `execute_approved()`'s dispatch branch;
      unit + integration tests (eligibility, execution, disconnected-at-approval-time failure).
- [x] Tasks: new `ScheduledTask.escalate_on_uncertainty` + `OrgSettings.task_escalation_cap_per_month`/
      `task_escalations_this_month`/`task_escalations_reset_at` columns; `_maybe_escalate_task()` +
      `_reset_monthly_escalation_count_if_due()`; `_verify_task_result()`'s OR-combine fix; Tasks page
      checkbox (conditionally rendered) + admin cap field; route-level + unit tests.
- [x] Shared `anthill/web/escalation.py`.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy` (clean set), full `pytest`
      (2093 passed).

## 5. Out of scope

- Any change to the retired third-party-vendor escalation path (`anthill/hybrid/*`,
  `OrgSettings.cloud_budget_usd`/`cloud_spent_usd`) - kept fully separate, conceptually and in code.
- A dollar-denominated budget for Task escalation - a call-count cap is the answer for now; revisit if
  reliable per-endpoint pricing data becomes available.
- A live, per-run escalation decision inside `scheduler._run_task()` beyond the fixed, creation-time
  `escalate_on_uncertainty` flag - explicitly ruled out (no one is present to approve anything mid-run
  on an unattended task).
- Any new confirm-first / blocking UI dialog anywhere.
- Extending the suggestion/escalation to council-active turns - a council-produced answer is never
  offered a redo, matching existing behavior in `wiki/ask.py`.
- Image/vision turns - excluded the same way the existing redo mechanisms already exclude them.
- Giving a Task's escalation re-answer its own tool set (mirroring the original run) - the v1 shared
  helper only builds a backend; each surface's assembly of goal/context/tools stays its own concern.
