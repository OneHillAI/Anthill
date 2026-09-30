# Project home: chats + tasks + agents + wiki

## Problem

First-class Projects (#419) makes a project read as "these chats + this wiki + this knowledge, for this
team." The project home (`/teams/{id}`) already showed the project's **chats**, **members**, and **wiki**
(review queue + link), and a chat started in a project inherits the project's `plane` + `team_id`
(scope-on-create, P2). But **Tasks** and **Agents** were missing from the home, even though both already
carry `team_id` and already run against the project wiki (P2 routing). So a project's scheduled work and
its standing workers were invisible on the one page that is supposed to be the project.

## Requirements

- The project home lists the project's **Tasks** and **Agents** (scoped by `team_id`), each in its own
  section mirroring "Chats in this project": a titled list with the item's status, plus an affordance to
  add one.
- Consistent with the existing chats list, the tasks/agents shown are the current user's items in the
  project; shared-across-members visibility is the same later refinement noted for chats.
- **Agents** get a "+ New agent in this project" action. Agent creation already supports team scoping, so
  the action links to `/agents?project={team_id}`, and the Agents page reads that query parameter to open
  the create form with the plane set to Team and the project preselected. The preselect is a no-op unless
  the id names a team the user can actually pick (degrades to the normal page).
- **Tasks** link to the Tasks surface (there is no standalone task-detail page; task creation is chat and
  proposal driven, and a task started from an in-project chat is already scoped to it).
- The empty states explain that in-project work runs on the project's plane and draws on the project wiki.

## Acceptance criteria

- `GET /teams/{id}` renders "Tasks in this project" and "Agents in this project" sections; a task and an
  agent with `team_id == id` appear there.
- The Agents section links to `/agents?project={id}`; visiting that opens the Agents create form with
  plane = Team and the project selected.
- Scoping is by `team_id` (and the current user), so items from other projects never appear.

## Scope

The project-home route (`team_detail`) gains two queries (tasks + agents by `team_id`); `team_detail.html`
gains the two sections; `agents.html` gains the query-parameter preselect. No model change - `ScheduledTask`
and `Agent` already carry `plane` + `team_id`. Shared-across-members visibility and a standalone
in-project task-create form are out of scope.
