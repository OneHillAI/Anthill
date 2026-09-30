# Intelligence settings: one model surface, compute as selectable cards

## Status

Design + first slice. The founder's target is **one "Intelligence" settings surface** (model & compute,
wiki, tuning, skills) whose view follows the account tier. This PR ships the headline of that design - the
**compute-as-cards** selector - and cleans up stale settings labels; the fuller unification (folding the
Org-hub model/wiki/tuning/skills into the same surface) is a follow-up.

## Design (target)

- **One surface** owns the *intelligence* layer: **Model & compute** (the core), plus **Wiki**, **Tuning**,
  **Skills** as siblings. Users, projects, integrations, and infrastructure stay in the Org hub.
- **Model & compute** is chosen from **selectable cards**, not a two-way radio. The account's one compute:
  1. **Local** - on this device (built).
  2. **Virtual private cloud** - your own connected endpoint (built; today's `solo_compute = cloud`).
  3. **Self-hosted** - a Mac mini or other box you run (coming soon).
- **One view per account, tier-driven.** There is NO Solo<->Org toggle: an account IS a Solo (personal
  workspace) or an Org (per the profiles / one-model-per-account model). A Solo account shows the three
  compute cards; **"Create an organization"** (the existing transition) flips the same surface to the org
  model. So the screen never tries to be both.

## This PR (first slice)

- `personalize.html` (Solo settings): the `Local | Cloud` radio becomes a **three-card selector** - Local
  and Virtual private cloud are selectable (mapping to the unchanged `solo_compute = local | cloud`);
  Self-hosted is shown as a **coming soon** placeholder that states the roadmap. No routing/backend change.
- Cleanup: the stale "Personalize" pointer on the Profile page becomes "Solo settings".

## Requirements (EARS)

- The Solo compute choice SHALL be presented as selectable cards (Local, Virtual private cloud, Self-hosted);
  Self-hosted SHALL be shown as not-yet-available.
- Selecting Local or Virtual private cloud SHALL persist `solo_compute` exactly as the prior radio did.
- Settings surfaces SHALL not present stale labels for renamed pages (e.g. "Personalize" -> "Solo settings").

## Follow-up

- Fold the Org hub's Cloud & model / Org wiki / Org skills / Tuning into the same tier-driven Intelligence
  surface, so model/wiki/tuning/skills each live in one place (view follows tier).
- Wire the Self-hosted compute option when its backend lands.

## Acceptance criteria

- `GET /personalize` renders three compute cards (Local, Virtual private cloud, Self-hosted),
  with `name="solo_compute"` on the two selectable ones and "coming soon" on the other one.
- Selecting Local / Virtual private cloud still persists `solo_compute` (existing tests hold).
- The Profile page shows "Solo settings", not "Personalize".
