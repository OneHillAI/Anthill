# Org-behaviour knobs live in the Org hub, not the personal-ish /settings page

## Status

Settings-rework phase (follows `settings-solo-home-consolidation.md`). Moves the org-behaviour knobs off
the this-device `/settings` page into a dedicated **Automation** page under the Org settings hub.

## Problem

`/settings` (the advanced "this-device inference" page reached from Solo settings) also carried three
**org-behaviour** knobs on a "Wiki & agent" card: `proactivity_mode` (event vs scheduled), the scheduled
`agent_interval_secs`, and `wiki_auto_promote` (auto-approve low-risk wiki promotions). These apply
org-wide and belong with the org's config, not on a personal/device page - the same fragmentation the
Solo-settings consolidation fixed for the personal side.

## Requirements (EARS)

- The org-behaviour knobs (`proactivity_mode`, `agent_interval_secs`, `wiki_auto_promote`) SHALL live on a
  dedicated **Automation** page reached from the Org settings hub, not on the `/settings` inference page.
- The `/settings` page SHALL retain only this-device inference knobs (Ollama URL, cache threshold), and
  its POST SHALL NOT write the moved knobs.
- Saving Automation SHALL persist the three knobs; `proactivity_mode` SHALL be guarded to
  `event | scheduled`.
- The Org settings hub SHALL link to the Automation page, and the rail's "Org settings" entry SHALL treat
  `/settings/automation` as an org-config sub-page (active state).

## Implemented

- New `GET/POST /settings/automation` (admin-only) + `settings_automation.html` with the three knobs and
  the events / Slack links that describe how inputs arrive.
- The "Wiki & agent" card is removed from `settings.html`; the `/settings` POST drops the three params.
- `org_settings_hub.html` gains an **Automation** card under "Model, knowledge & tuning"; `_sidebar.html`
  adds `/settings/automation` to the Org-settings active path.

## Acceptance criteria

- `GET /settings/automation` renders and contains `proactivity_mode`, `agent_interval_secs`, and
  `wiki_auto_promote` controls.
- `POST /settings/automation` persists all three (invalid `proactivity_mode` coerces to `event`).
- `GET /settings` no longer contains those three field names.
- The Org hub (`/settings/org`) links to `/settings/automation`.
