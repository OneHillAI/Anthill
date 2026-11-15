# Spec: a Solo workspace does not talk about an organisation

Status: implemented. Lane: `pillar:feature`. Issue: #158.

## Problem

A user who chose "Just me" at setup still met organisation language on several pages, and a back link to a hub
they never created (#158):

- Agents: "org-wide defaults for new agents". Tasks: "Task settings for administrators", "your organisation's
  connected cloud model". Metrics: "How your org is using Anthill", "An admin sets the per-query rates". Audit
  log: "sent to org admins". Profile: "how you appear to teammates" and "role admin". Projects: "the org wiki in an
  organization", and on a project's page "promotions to the org are approved by the project owner".
- Projects, Usage metrics, Audit log and the other pages in `_ORG_SUBSETTINGS` showed "Manage organisation"
  (to `/settings/org`), because `_back_nav_for` had no Solo case.

## Requirements

1. A workspace is Solo when its deployment topology is `solo` (`_is_solo(cfg)` in `anthill/web/app.py`), the same
   test the sidebar uses to hide the organisation name. The Agents, Tasks, Projects, project, Metrics, Audit log and
   Profile routes pass it to their templates as `is_solo`.
2. For Solo those pages use plain words: "you", "your workspace", "the account owner". Nothing else on the page
   changes, and the text an organisation sees is unchanged, character for character.
3. The Profile page hides the role line for Solo (the only account is its own admin; the word means nothing there).
4. `_back_nav_for(path, solo=True)` sends Projects, Metrics and Skills back to the Dashboard, a project's page back
   to Projects, and the other organisation sub-pages (Audit log, Training, Backup and so on) back to Settings.
   `_nav_context` passes the workspace's `solo` flag. An organisation still gets "Manage organisation".
5. No access check changes: the admin-only controls (agent defaults, task settings, metric rates) are shown to the
   same people as before. Only their wording differs.

## Known limits

- The Solo Projects page and a Solo project's page still say "set up an organisation later" (and the second keeps
  the older spelling "organization", which `tests/test_project_surface.py` pins). It is a pointer to a feature, not
  a claim that the user has one.
- The notice shown after a refused task action ("an admin for an organisation task") is shared with organisations
  and unchanged. A Solo user only reaches it by opening a task link that is not theirs.
- Spelling is not unified here. "organization" (setup, dashboard) against "organisation" (Settings, navigation) is
  a separate sweep.
- A Solo account that has connected its own cloud endpoint is still Solo for this wording. Behaviour that follows
  `planes.is_org_mode` (inviting members to a project, the Organization plane option) is unchanged.

## Acceptance

- Rendered as a Solo admin, the Agents, Tasks, Projects, project, Metrics, Audit log and Profile pages contain no
  "org", "organisation", "admin", "administrator" or "teammate" in their visible text (Projects and a project's page
  may say "organization" once, in the pointer above).
- Rendered as an organisation admin, the same pages still contain each of the original sentences.
- `_back_nav_for` gives the Solo mapping above and the unchanged organisation mapping; a Solo leaf page renders
  "Dashboard" or "Settings" in its back link and no "Manage organisation".
- Tests: `tests/test_solo_wording.py` and `tests/test_subsettings_back_nav.py` (model-free).
