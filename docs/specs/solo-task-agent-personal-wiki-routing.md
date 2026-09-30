# Spec: Solo tasks and agents ground in the per-user personal wiki

Status: implemented
Lane: `pillar:knowledge`
Relates to: `#838`, `docs/specs/project-wiki-routing.md` (the shared `run_wiki_workspace` resolver this
depends on).

## 1. Introduction

QA's alpha smoke test found that a Solo Task or Agent could not see knowledge its own creator had built
through the app: uploading a document via `/wiki/upload`, saving a snippet to the wiki, or promoting a
memory to the wiki all write to that user's per-user personal wiki (`wikis/user-<id>`), and Chat grounds
in it correctly - but a Task or Agent run by the same user read the legacy, single-node
`ANTHILL_WORKSPACE` instead, which nothing in the current app ever writes to. The knowledge was real and
correctly stored; only Tasks and Agents couldn't find it.

Root cause: `run_wiki_workspace()` (`anthill/web/agent_context.py`) - the one shared resolver every
surface calls for its wiki read/write base - only returns the per-user personal wiki when its caller
passes `personal_user_id`; omitted, it falls back to `ANTHILL_WORKSPACE`. Chat's call site passes it.
The scheduler's task runner and the agent runner's tool-building step did not.

## 2. Requirements

- THE SYSTEM SHALL resolve a Solo task's or agent's wiki workspace to its creator's own personal wiki
  (`wikis/user-<id>`), matching what Chat already resolves for the same user.
- THE SYSTEM SHALL NOT change Org or Team-plane routing: `run_wiki_workspace`'s own branching already
  returns the team wiki (team-plane, active member) or the org wiki (a non-personal-context run) before
  ever consulting `personal_user_id` - passing it unconditionally, as Chat's call site already does, is
  therefore safe for every plane, not just Solo.
- A regression test SHALL assert the actual resolved path equals the per-user personal wiki - not merely
  that it isn't a team-scoped path, which the legacy workspace also satisfies and is why the existing
  suite missed this (see below).

## 3. Design

- One added keyword argument at each of the two affected call sites: `personal_user_id=task.created_by`
  (`scheduler.py`'s `_run_task`) and `personal_user_id=agent.created_by`
  (`agents_run.py`'s `_plane_tools`) - matching the pattern `chat_stream` already uses
  (`personal_user_id=_uid`).
- No change to `run_wiki_workspace` itself: its resolution logic was already correct for every case it
  was actually given; the bug was purely two callers not supplying an argument the function has always
  accepted.

## 4. Why the existing suite didn't catch this

`test_project_agent_writes_to_its_own_project_wiki` (`tests/test_project_wiki_routing.py`) already
exercised a Solo agent through `_plane_tools`, but only asserted `"team-" not in ws_solo` - true whether
`ws_solo` was the correct per-user wiki or the legacy workspace, since neither contains `team-`. It
passed unchanged both before and after this fix. The scheduler side had no equivalent test at all.

## 5. Tasks

- [x] `personal_user_id` added to both call sites (R1, R2).
- [x] `test_solo_agent_reads_the_creators_own_personal_wiki_not_the_legacy_workspace` /
  `test_solo_task_reads_the_creators_own_personal_wiki_not_the_legacy_workspace`: assert the resolved
  path equals `workspace_for("personal", user_id=...).root` exactly, not just "isn't a team dir".
  Verified both fail without the fix (resolve to the legacy workspace) and pass with it.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 6. Out of scope

- **Org/Team-plane behaviour** - unaffected; `run_wiki_workspace`'s existing branching already handles
  those before `personal_user_id` is consulted, and both new tests only assert the Solo case.
- **Backfilling already-run task/agent history** - this only changes what a NEW run resolves to; a past
  run's results (already produced against the legacy workspace) are not retroactively regrounded.
