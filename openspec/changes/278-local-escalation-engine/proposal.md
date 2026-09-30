# Issue #278: local-to-provider escalation engine

## Why

"Detect when a local answer is weak and route the query to the org GPU backend automatically" -
decided, not yet built. A solo-topology account whose default is local (cost-saving) but that has also
connected an org/RunPod/inference-provider endpoint should be able to answer one uncertain turn on the
stronger backend, without changing its default and without a silent auto-escalation.

## Blocking fix, done first

`plane_routing.plane_inference()`'s ONE-MODEL-PER-ACCOUNT branch routed *any* account with a validated
endpoint to it for every Solo turn, regardless of `solo_compute` - making the target scenario
unreachable (there was never a local answer to escalate from). Narrowed to require
`deployment_topology == "org"` too, so genuine multi-user orgs are unaffected.

## What this change adds

- **Chat**: a natural-language redo phrase ("use the cloud model") on a follow-up to an uncertain local
  answer, reusing the existing `redo_mode()`/`_should_suggest_deeper` mechanism - no new UI. Also fixes
  a real pre-existing bug: the "web"/"deep" redo detection was gated on a query parameter the shipped UI
  never populates.
- **Agents**: propose (never silently perform) escalating a follow-up run via the existing
  `AgentApproval` governance gate.
- **Tasks**: a creation-time-only `escalate_on_uncertainty` choice, bounded by an org-wide monthly
  call-count cap, with honest `verify_needs_review` flagging when eligible but not escalated.
- Shared `anthill/web/escalation.py` plumbing (force-resolve org plane, build a one-off backend, audit
  log) used by both Agents and Tasks - PII scrubbing and audit logging are automatic via the existing
  `OpenAICompatBackend`/`log_inference_call` machinery.

## Spec

`docs/specs/278-local-escalation-engine.md` (new).
