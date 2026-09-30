# Project home: shared tasks and agents, per-member chats

## Problem

The project home (`/teams/{id}`, #419) lists the project's chats, tasks, and agents. The first cut (#553)
scoped all three to the current user (`created_by`/`user_id == uid`), matching how chats had always
worked. But a project's **tasks** and **agents** are shared team infrastructure - a team's weekly digest
task, its standing research agent - so a member who did not create them could not see them on the project
home, which undercuts "a project = the team's work." **Chats**, by contrast, are personal conversations:
even inside a project, surfacing every member's chats would expose half-formed exploration.

## Design

- **Tasks and agents are shared** across the project's members: the project home shows every item with
  the project's `team_id`, regardless of who created it.
- **Chats stay per-member**: the project home shows only the current user's chats in the project.

Access is coherent with this visibility:
- Only active members reach `/teams/{id}` (the existing `_team_role` gate), so shared items are shown only
  to members.
- **Tasks** are already org-visible everywhere (the `/tasks` list, task result, and task actions all scope
  by `org_id`, not creator), so no task access change is needed.
- **Agents** were viewable only by their creator (or anyone for an org-plane agent). `agent_detail` now
  also lets an **active member of the agent's project** view a team-plane agent, so a shared project agent
  opens from the project home. Viewing is read-only for non-creators: `_agent_for_write` (run/pause/delete/
  edit/approve) is unchanged - modifying a project agent stays with its creator or an org admin.

## Acceptance criteria

- On `/teams/{id}`, a task and an agent created by one project member are visible to another member.
- On `/teams/{id}`, a chat created by one member is NOT shown to another member.
- A project member can open (`GET /agents/{id}`, HTTP 200) a team-plane agent created by another member;
  the write controls are absent (read-only) for a non-creator.
- A non-member still cannot view a team-plane agent (`is_active_member` is false), unchanged from the P2
  trust boundary.

## Scope

Two query filters on the `team_detail` route (drop `created_by` for tasks + agents) and the view gate in
`agent_detail` (allow active project members). No model change, no change to write authorization, and no
change to chats. Whether a member should be able to run or pause a shared project agent (not just view it)
is a separate policy question, left for a follow-up.
