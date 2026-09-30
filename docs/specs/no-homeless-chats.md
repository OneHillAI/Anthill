# No homeless chats: the three homes in the chat rail

## Problem

Every chat carries a `plane` (`solo | team | org`), but the chat sidebar only split into two buckets:
`org` and "everything else". The "everything else" bucket was labelled **Solo (private)**, so a
**team**-plane chat was shown as if it were a private solo chat. There was no Project home in the rail at
all. A chat that belonged to a team had no correct place to live: it was either mislabelled Personal or,
if you had left the team, effectively invisible. This is the "homeless chat" gap flagged in the
first-class-Projects review (`OneHillAI/internal#8`, `engineering-plans/FIRST_CLASS_PROJECTS.md`, product
issue #419).

## Design (from the first-class-Projects P0 decision)

Make the plane itself the home, with three navigable tiers that map 1:1 onto the existing `plane` column
(this is naming/IA, not a schema change; migration is a no-op):

| Home (rail heading) | Plane | team_id | What it is |
|---|---|---|---|
| **Personal** | solo | NULL | the user's private, always-local space; folders group it |
| **a Project** (named) | team | set | a team's shared chats; one heading per project |
| **Organization** | org | NULL | the org-wide space (org cloud model + org wiki); org accounts only |

A loose "+ New chat" still lands in Personal (solo). Nothing changes tier silently.

## Requirements

- The chat rail groups unfiled chats under their home: Personal (solo), one section per Project (team,
  named from the user's teams), and Organization (org). No chat is shown under the wrong home, and no chat
  is dropped.
- A team chat whose project is not in the user's current team list (e.g. they left the team) still gets a
  home under a generic "Projects" heading, never Personal.
- Personal-only users (no org backend, no team chats) keep the simple flat list with no home headings -
  the homes only appear once there is more than the Personal home in play.
- Folders remain a Personal-only sub-grouping; they do not nest under a project.
- Pinned chats stay pinned at the top (a cross-home shortcut) and are not duplicated in their home.
- Each home's chats carry a distinct plane dot: Personal (solo) green, Project (team) clay, Organization
  (org) amber.

## Acceptance criteria

- With an org backend available, the rail renders home headings (`rail-conv-section`) including
  **Personal**, and a solo chat appears under Personal with `plane-dot-solo`.
- A `team`-plane chat appears under its project's heading (the team name) with `plane-dot-team`, never
  under Personal.
- An `org`-plane chat appears under **Organization** with `plane-dot-org`.
- Without an org backend and with only personal chats, the rail is a flat list with no `rail-conv-section`
  headings (unchanged behaviour).

## Scope

Rail IA only (`_sidebar.html` + one plane-dot CSS rule). It reuses data already present on every render:
the conversation's `plane`/`team_id` and the `nav_teams` list from `_nav_context`. The broader
first-class-Projects work (project home pages, scope-on-create, per-project settings) is P1-P3 in
`engineering-plans/FIRST_CLASS_PROJECTS.md` and is out of scope here.
