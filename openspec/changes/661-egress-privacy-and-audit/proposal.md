# PR #661: egress PII scrubbing, per-turn audit trail, local/remote indicator

## Why

Once an account connects Berget, RunPod, or any onprem endpoint, every chat/task/agent turn on that
plane sends the raw wiki context, conversation history, and question straight to
`OpenAICompatBackend`, unredacted - the PII scrubber that already exists in this codebase
(`anthill/hybrid/scrub.py`) was built for a different, now-retired feature and was never wired to the
call site that actually carries real traffic today. Separately, nothing records which model/endpoint
answered a turn, and nothing tells the user, in the chat itself, whether an answer stayed on their
device.

## What this change adds

- Outbound PII scrubbing + inbound restoration (streaming-safe) in `OpenAICompatBackend`, skipped for
  loopback/on-device endpoints.
- A new `stays_local(backend, base_url)` helper (`anthill/inference/base.py`), shared by the scrub gate
  and the new UI indicator.
- A new `inference.call` audit event (`anthill.web.audit.log_inference_call`) at all 6 real call sites
  of `plane_inference()` - chat, the Slack/Discord/widget path, agent runs, agent-approval
  continuations, task runs, and skill distillation.
- A new `ChatMessage.answered_locally` column, set at both assistant-message save sites, surfaced live
  via an SSE meta event and rendered as a small badge in the chat UI (server-rendered + live JS).

## Out of scope

A live, per-turn "escalate this turn from local to the inference provider" decision engine - discussed
alongside this change, not built here. This change is the plumbing (know which model answered, keep
the data clean) that design would sit on top of. See
`docs/specs/661-egress-privacy-and-audit.md` §5 for the full out-of-scope list.

## Spec

`docs/specs/661-egress-privacy-and-audit.md` (new); `docs/specs/model-onboarding-and-sovereignty.md`
R6/R9 updated in place.
