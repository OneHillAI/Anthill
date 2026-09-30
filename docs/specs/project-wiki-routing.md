# Spec: per-project wiki routing

Status: proposed
Lane: `pillar:knowledge`
Relates to: `OneHillAI/Anthill#419` (first-class Projects), the internal
`engineering-plans/SOLO_PROJECT_ORG_SETUP.md` (Solo/Project/Org, P2 / "Gap A").

## 1. Introduction

A **Project** (the surfaced `Team`) is meant to tie its chats, tasks, agents, and its **own wiki**
together, so "the knowledge here is this project's, used only for it". Projects were surfaced in the UI
(#419 P1) and scoped on creation (`Conversation.team_id` etc.), but at **runtime** the promise leaked:

- A project run's write tools (`write_page`, files) wrote to the user's **personal** or **org** wiki,
  never the project's `team-<id>` wiki.
- Grounding (principles + skills + read context) pulled in **every** team the user belongs to, not the
  one project the run is in - `context_workspaces` even documented `team_id` as "accepted but unused".

So a project could neither keep its own knowledge nor stay separated from the user's other projects. This
spec makes per-project wiki routing true at runtime across all three surfaces (chat, tasks, agents).

## 2. Requirements

### R1 - A project run grounds in its own wiki only
- WHEN a run has `plane == team` AND a `team_id`, THE SYSTEM SHALL scope its principles/skills/read
  grounding to **that project's** wiki (`team-<id>`), plus the org wiki (read-only) in an org and the
  personal wiki when on local compute - and SHALL NOT include the user's other projects' wikis.
- WHERE a run is not in a project (solo, or org), THE SYSTEM SHALL keep its existing broad grounding
  (org + the user's teams + personal-when-local) unchanged.

### R2 - A project run writes to its own wiki
- WHEN a chat, task, or agent run has `plane == team` AND a `team_id`, THE SYSTEM SHALL resolve the write
  workspace (the `make_tools` target) to that project's `team-<id>` wiki.
- WHERE a run is Org, THE SYSTEM SHALL write to the org wiki; WHERE Solo, to the personal/default
  workspace - both unchanged.

### R3 - Authorization is the trust boundary
- THE SYSTEM SHALL route to a project's wiki (read or write) ONLY WHEN the run's user is an **active
  member** of that project.
- IF a supplied `team_id` names a project the user is not an active member of, THEN THE SYSTEM SHALL NOT
  ground in or write to that project's wiki (it falls back to the non-project path) - a manipulated or
  stale `team_id` can never reach another project's wiki.

### R4 - Privacy invariants preserved
- THE SYSTEM SHALL keep the plane privacy contract: personal context/wiki is included only on local
  compute; a project chat blends the org wiki read-only in an org, never another project.

## 3. Design

- **One shared resolver** - `anthill/web/agent_context.py::run_wiki_workspace(db, *, plane_inf, team_id,
  member_user_id, personal_user_id=None)` is the single place that resolves a run's wiki workspace
  (read+write base): the project's `team-<id>` wiki when the run is in a project the member belongs to
  (R2, R3), else the org wiki (cloud) or the personal/default workspace (Solo/local). `scheduler.py`
  (`_run_task`), `agents_run.py` (`_plane_tools`), and the chat path in `app.py` all call it, so the
  three surfaces cannot drift and use the SAME membership predicate `is_active_member` (R3).
- **Grounding** - `context_workspaces` scopes a team-plane run to its own `team_id` (R1), guarded by
  `is_active_member` (R3); non-project runs keep the all-teams loop. `agent_context_for` already passes
  `team_id`.
- **Chat read blend** - `app.py` `extra_ws`: a project chat blends the org wiki read-only in an org, OR
  the user's own personal wiki when on local compute (R1, R4); never the user's other projects.

## 4. Tasks

- [x] `is_active_member` + the shared `run_wiki_workspace` resolver (R2, R3).
- [x] `context_workspaces` project scoping (R1, R3).
- [x] Route the write/read base through the resolver in `scheduler.py`, `agents_run.py`, `app.py` (R2).
- [x] Chat `extra_ws` project scoping incl. local personal-wiki blend (R1, R4).
- [x] `tests/test_project_wiki_routing.py`: the resolver across every surface, grounding scoping, and
  non-member denial (read + write).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (on merge).

## 5. Out of scope

- **Project setup choices** - "belongs to Solo or Org" at creation and an explicit "connect a Solo/Org
  parent wiki" option are a later P2 slice; this spec covers the runtime routing only.
- **Per-project tuning / model** - a project always borrows its parent's model (no per-project model).
- Migrating existing conversations' historical grounding; this changes routing from now on.
