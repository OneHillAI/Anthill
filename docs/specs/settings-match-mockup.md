# Settings and nav: match the approved mockup

Status: proposed. Lane: `pillar:platform`. Ships in slices: (A) the Settings page content, (B) the
Manage-Org view, (C) the nav two-group + Settings sub-items + footer overflow.

Refines `docs/specs/declutter-onboarding-and-org-settings.md` (shipped). That work moved the compute/model
choice into Settings; a live review found the result is still the SETUP picker, not the mockup's SETTINGS
screen: This device shows the full compute-card picker + a raw, unfitting model dropdown + a "Skip for now"
button that jumps to chat; Intelligence is an unclear mix of persona + personal-knowledge links + tuning;
Privacy is an empty text card; and Organisation's "Manage organisation" opens the old `/settings/org` grid.
Style drifted toward the mockup; content did not. This spec re-points Settings at the mockup's content.

## Principle

Every control shown SHALL either work against a real backend or be clearly marked "coming soon". No dead
controls (the review's central complaint: a "Your cloud" card that does nothing, a "Skip for now" that
belongs to setup). Settings is a summary + preferences screen; the full compute picker is a change-flow
reached from a "Change where it runs" action, not inline.

## A. Settings page (`/personalize`)

Four tabs, matching the mockup (`scratchpad/design/anthill-target.html`, `#viewSettings`):

- **This device**: a *Where your AI runs* summary card (on your machine / your cloud + the model, with a
  "Change where it runs" action that opens the existing compute picker) · *Answer style* (Faster/Balanced/
  Smarter - **coming soon**, no routing pref backend yet) · *Appearance* (follows your system today; manual
  Light/Dark **coming soon**) · Advanced (Model storage with the real on-disk size; Custom local server
  **coming soon**). Removes the inline picker, the raw model dropdown, and the "Skip for now" button
  (Settings only Saves).
- **Intelligence**: *Your council* (the current model(s) + a "Change models" action to the model picker,
  filtered to what fits) · *Knowledge* (Living wiki + Memory toggles - Memory maps to `auto_memory_off`;
  Sources shows the wiki count and links to add) · *Self-tuning* (real gold-example progress) · *How your
  AI sounds* (the persona editor, backed by the existing `User.profile` field - kept here per the review).
- **Privacy**: *Improve my model from my answers* (`auto_memory_off`) · *Scrub personal details before
  training* (`cloud_scrub_pii`) · *Web access* (**coming soon**) · a *What leaves your setup* summary.
- **Organisation** (admin): a short summary + "Manage organisation" that opens the Manage-Org view (B),
  not the old `/settings/org` grid.

## B. Manage-Org view

A clean dedicated admin view (mockup `#viewOrg`) replacing the old `/settings/org` card grid: tabs
**Members · Model & compute · Knowledge · Integrations · Metrics**. Reuses the existing org routes/data
(users, org council, org wiki, connectors, metrics) behind a mockup-matching presentation; no new engine.

## C. Nav + footer

- The rail splits into two groups: **Dashboard · Chat · Tasks · Agents**, a divider, then **Knowledge ·
  Projects · Settings**; when the user is in Settings, its sub-sections (This device / Intelligence /
  Privacy / Organisation) show as rail sub-items.
- The footer collapses to a **profile chip** (name · Solo/Org) with Sign out + account under it; the
  Contribute / How it works / Setup / Take a tour links move into a small **Help overflow** so they are one
  click away rather than a pile.

## Out of scope / coming soon (honest, not dead)

- Answer-style routing preference, a user-level Web-access toggle, and a manual Light/Dark override are
  presented as "coming soon" until their backends exist; they are not shipped as non-functional toggles.
- No change to the model/compute engine, the knowledge engines, or org provisioning; this is presentation
  + wiring of existing fields.

## Acceptance criteria

- Settings This device shows a summary (not the full picker), no raw model dropdown, and no "Skip for now";
  "Change where it runs" reaches the picker.
- Intelligence shows council + knowledge toggles + self-tuning + a working persona editor; the persona
  round-trips through `User.profile`.
- Privacy's Memory and Scrub-PII toggles persist to `auto_memory_off` / `cloud_scrub_pii`; Web access is
  labelled coming soon.
- Organisation's "Manage organisation" opens the new Manage-Org view, not `/settings/org`.
- The rail shows the two groups with Settings sub-items; the footer is a profile chip + a Help overflow.
- No control shown is a dead no-op; every non-wired control is labelled coming soon.
