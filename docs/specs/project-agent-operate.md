# Project members can operate a shared agent

## Problem

A project's agents are shared team infrastructure, and the project home now shows every member the
project's agents (`docs/specs/project-shared-visibility.md`, #558). But a fellow member could only *view*
a shared agent - the Run now / Pause controls were gated by `_agent_for_write` (creator or org admin), so
a member could see a team's standing agent but could not run or pause it. That is the wrong split for
shared infrastructure: operating a team agent is a normal member action; only changing or removing it is
an owner action.

## Design

Split agent authorization into two levels:

- **Operate** (run now, pause, resume): anyone who may modify the agent, PLUS an active member of a
  team-plane agent's project. A new `_agent_for_operate` helper wraps `_agent_for_write` and adds the
  team-member case via the existing `is_active_member` trust boundary.
- **Modify** (edit, delete, approve/reject its pending actions): unchanged - `_agent_for_write` (creator,
  or an org admin for an org-plane agent). Config, destructive, and governance actions stay with the
  owner.

The agent detail page gains `can_operate` alongside `can_write`: Run now and Pause/Resume render under
`can_operate`; Delete stays under `can_write`. So a fellow project member sees a runnable-but-not-editable
agent.

## Acceptance criteria

- A `run-now` or `toggle` POST by an active member of a team-plane agent's project succeeds (the agent's
  status/next-run update).
- A `delete` (and `edit`, `approve`, `reject`) POST by that same member is rejected - the agent is
  unchanged.
- A non-member still cannot operate or view the agent (the trust boundary is unchanged).
- On the agent detail page, a fellow member sees Run now + Pause but not Delete.

## Scope

Adds `_agent_for_operate`; points the `run-now` and `toggle` routes at it; adds `can_operate` to the agent
detail context and splits the template's action block. No model change; edit/delete/approve authorization
is untouched. Builds on #558 (a member must be able to see the shared agent to reach it), so it stacks on
that PR.
