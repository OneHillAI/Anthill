# Solo framing of personal projects

## Problem

First-class Projects (#419) makes a Project the existing Team, surfaced - and a Team can exist on a Solo
account (no org backend) as a personal, single-user, always-local project (`org_id`-scoped but no shared
backend). But the Projects page (`/teams`, `teams.html`) explained a project only in organization terms:
"the middle tier between your personal space and the shared org", "you invite existing org members", "the
project owner approves anything promoted out", "personal to project to org". None of that applies on a
Solo account, where a project is your own and never leaves the device. The P0 design's "Solo explains
personal projects" item was unbuilt.

## Requirements

- The Projects page frames a project differently by account type, keyed off `is_org_mode(cfg)` (an org
  backend has been configured):
  - **Org account:** the existing framing - a shared team space, the middle knowledge tier, invite org
    members, owner-approved promotion, personal -> project -> org.
  - **Solo account:** a project is your own - single-user, always local, with its own wiki and memory that
    never leave the device, sitting alongside your personal space. Note that setting up an organization
    later turns projects into shared team spaces.
- The create-form note and the empty state follow the same split (no "invite org members" or "for a team"
  language on a Solo account).
- No behaviour change: this is framing only. The invite affordance on the project home is already gated to
  org accounts (a direct invite POST to a Solo project is rejected, #419 review).

## Acceptance criteria

- `GET /teams` on an org account (org backend configured) shows the org framing, including "invite
  existing org members".
- `GET /teams` on a Solo account (no org backend) shows the personal-project framing, including "always
  local" and "single-user", and does not mention inviting org members.

## Scope

Copy + one context value (`is_org`) on the `/teams` route; no model or routing change. Isolation of
Solo/personal-project data from the org cloud is already enforced and tested
(`tests/test_project_wiki_routing.py`, `tests/test_plane_routing.py`, `tests/test_tiers_context.py`).
