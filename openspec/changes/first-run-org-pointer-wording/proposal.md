# First-run page says to set up an organisation "from the dashboard"; the dashboard has no such link

Lane: `pillar:platform`. Priority: low (one sentence on a sign-up page; nothing is broken). Category: bug (wording).
Full spec, added by the PR that builds this: `docs/specs/first-run-org-pointer.md`.
Issue #241. Found by the daily docs check of 2026-11-17. The docs side is #239.

## Why

The first-run page says an organisation can be set up "later from the dashboard". The Solo dashboard has no such
control; the only place is Settings, on the Organisation tab. Two code comments repeat the wrong place.

## What changes

- `setup.html`: "you can set up an organisation later in Settings, under Organisation." Text only.
- `anthill/web/app.py`: the two comments say Settings.
- A test pins the sentence and the Settings control it points to.
- A `changelog.d/241.fixed.md` fragment, this folder and the spec.

## Decision (answered 2026-11-18)

Words only. No link or card is added to the Solo dashboard.

## Guardrails (do NOT touch)

- The organisation set-up flow, route, name requirement, two-click confirm, and who may do it.
- The Solo dashboard, the account-type screen, when the sentence is shown, any other wording.
- `docs/`: the user-guide side is #239.

## Impact

One template line, two comments, one test, one changelog fragment, the spec and this folder. No migration, no new
dependency, no behaviour change.
