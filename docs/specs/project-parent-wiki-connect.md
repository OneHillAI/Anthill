# Spec: connect a project to its parent wiki

Status: proposed
Lane: `pillar:knowledge`
Relates to: `OneHillAI/Anthill#419`, `docs/specs/project-wiki-routing.md`,
the internal `engineering-plans/SOLO_PROJECT_ORG_SETUP.md`.

## 1. Introduction

Per-project wiki routing (`project-wiki-routing.md`) made a project read + write its **own** wiki. The
founder's model also asks that a project can, **at setup**, choose whether to **connect to (also read)
its parent wiki** - the Solo (personal) wiki for a Solo project, or the Org wiki for an Org project:

> "projects can connect/add to a solo/org wiki at setup depending to where the project belongs".

Today that blend is always-on (an Org project always reads the org wiki read-only; a Solo project always
blends personal). This spec makes it an explicit, per-project choice so a project can be **fully isolated**
(its own knowledge only) when that is what the work needs, while keeping the connected default.

## 2. Requirements

### R1 - A per-project "connect parent wiki" choice, defaulting to connected
- THE SYSTEM SHALL store, per project, whether it connects to its parent wiki (`Team.connect_parent_wiki`,
  default **true** so existing projects and the current behaviour are unchanged).
- WHEN creating a project, THE SYSTEM SHALL let the creator choose to connect the parent wiki or not.
- WHERE a project owner opens Project settings, THE SYSTEM SHALL let them change the choice later.

### R2 - The choice governs read grounding only
- WHEN a run is in a project AND the project connects its parent, THE SYSTEM SHALL blend the parent wiki
  **read-only** into the run's grounding: the **org** wiki for an Org project, or the user's **personal**
  wiki for a Solo (local) project.
- IF a project does NOT connect its parent, THEN THE SYSTEM SHALL ground the run in the project's own wiki
  ONLY (plus never another project) - full isolation.
- THE SYSTEM SHALL NOT change the write target: a project always writes to its own `team-<id>` wiki
  (`project-wiki-routing.md` R2), regardless of this choice.

### R3 - Isolation is not a leak of the boundary
- THE connect choice SHALL only ever ADD the run's own parent (org for org-projects, personal for the
  requesting user) - it SHALL NOT expose any other project's wiki, and SHALL compose with the active-
  membership boundary of `project-wiki-routing.md` R3.

## 3. Design

- **`anthill/web/db.py`** - `Team.connect_parent_wiki` (Boolean, default True; additive, auto-migrated by
  `ensure_columns`).
- **`anthill/web/agent_context.py`** - `context_workspaces` (principles/skills grounding): for a project
  run, include the org workspace only when the project connects its parent; a disconnected project drops
  the org layer. A small `project_connects_parent(db, team_id)` helper reads the flag.
- **`anthill/web/app.py`** - the chat read blend (`extra_ws`) for a project chat blends the org wiki
  (org project) or the personal wiki (local project) only when the project connects its parent.
- **Create + settings** - `POST /teams` accepts `connect_parent_wiki`; the create form and Project
  settings expose the toggle with a plain-language label.

## 4. Tasks

- [x] `Team.connect_parent_wiki` column (R1).
- [x] `project_connects_parent` helper + honour it in `context_workspaces` and the chat blend (R2, R3).
- [x] `POST /teams` + create form + Project settings toggle (R1).
- [x] `tests/test_project_parent_wiki.py`: default connected; a disconnected project drops the parent
  blend but keeps its own wiki; the write target is unchanged; the flag round-trips through create +
  settings.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (on merge).

## 5. Out of scope

- **Per-project compute/tier** (a project running on the Solo *local* model inside an Org install) - the
  "which model/skills it borrows" belongs-to-Solo-or-Org decision is a separate slice; this spec covers the
  wiki-connect half of "belongs to solo/org at setup".
- Connecting a project to *another* project's wiki - the parent is only the run's own Solo/Org wiki.
