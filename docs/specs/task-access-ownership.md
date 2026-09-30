# Spec: Tasks are owned, not org-wide (fix the task IDOR)

Status: implemented. Lane: `pillar:privacy`. Issue: #597.

## Problem

A `ScheduledTask` carries a `plane` (solo | team | org) + `created_by` - it is owned, exactly like an
Agent. But every task-by-id endpoint (and the listing) resolved the task with a bare `org_id` filter and
never checked the owner:

```python
task = db.query(ScheduledTask).filter(
    ScheduledTask.id == task_id, ScheduledTask.org_id == org.id).first()
```

So ANY member of an org could reach ANY other member's task, including private Solo ones. A cross-user
access gate (`qa/chat-eval/idor_gate.sh`) confirmed it: a peer member could `GET /tasks/{id}/result`
(200, full task + run history, whose outputs can carry private/wiki/cloud data) and
`POST /tasks/{id}/cancel` (flipped it to cancelled), plus edit / run-now / queue - and so could the admin.
Chats (`user_id ==`) and agents (`created_by ==`) were already owner-scoped; tasks just omitted the check.

## Requirements

Task access must be owned and plane-aware, mirroring the agent access model:
- **view** (result / listing): the creator, any org member for an org-plane task, or an active member of a
  team-plane task's project. A Solo task is private to its creator.
- **write** (edit / cancel): the creator, or an org admin for an org-plane task.
- **operate** (run-now / queue): everyone who may write, plus an active member of a team-plane task's
  project (shared team infrastructure).

## Design

Three helpers next to the task endpoints, the task twins of `_agent_for_write` / `_agent_for_operate` /
`agent_detail.can_view`:
- `_task_visible(db, task_id, org, user)`
- `_task_for_write(db, task_id, org, user)`
- `_task_for_operate(db, task_id, org, user)`

The three checks share one `_task_in_org(db, task_id, org)` fetch. Applied at every site: the listing
(a user's own tasks + all org-plane + the team-plane tasks of the projects they belong to, matching
`_task_visible` so nothing shown by the list is then denied on click), `task_result` -> visible,
`edit_task` + `cancel_task` -> write, `run_task_now` + `queue_task_input` -> operate.

## Verification

- `qa/chat-eval/idor_gate.sh`: 13/13 (was 3 task failures) - a peer and the admin are now denied read +
  mutate on B's task; chat/folder/agent unchanged; controls (B on B's own) still pass.
- `tests/test_task_access.py`: a Solo task is private to its creator (peer + admin denied); an org-plane
  task is shared read but admin/creator write.
- `tests/test_task_planes.py` + `test_task_run_history.py` + `test_teams.py` + `test_planes.py` green (no
  regression to legitimate task access).
