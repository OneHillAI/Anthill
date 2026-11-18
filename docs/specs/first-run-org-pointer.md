# Spec: the first-run page points to where an organisation is really set up

Status: implemented. Lane: `pillar:platform`. Issue: #241. Related: #239 (the user-guide side), #192 (spelling), #204 (Solo and Org stay separate).

## Problem

The first-run page (`anthill/web/templates/setup.html`) tells a person who is setting up for a team: "Create your
personal account first - you can set up an organisation later from the dashboard." The Solo dashboard has no such
control. It shows what the account runs on, the model or council, an audit-log link and a how-it-works link. The only
place is Settings, on the Organisation tab (`personalize.html`, `data-st="org"`, admin only), in the "Set up an
organisation" card: a required name, a two-click confirm, no way back to Solo (route
`/personalize/organization/setup`). A person who follows the sentence looks on the dashboard and finds nothing.

The older spec `docs/specs/signup-no-org.md` planned an entry on the dashboard. The control later moved to Settings and
that spec was not changed; it is left as the record of the original plan.

## Requirements

1. The sentence on the first-run page SHALL name Settings and the Organisation tab, and SHALL NOT name the dashboard:
   "you can set up an organisation later in Settings, under Organisation."
2. The page SHALL stay text only (no link: it is shown before sign-in).
3. The two code comments in `anthill/web/app.py` that repeat "from the dashboard" SHALL say Settings, Organisation tab.
4. A test SHALL fail if the sentence names the dashboard again, or if the Settings template loses the Organisation tab
   or its "Set up an organisation" card.
5. Nothing else SHALL change: not the set-up flow, its route, the name requirement, the two-click confirm, who may do
   it, when the sentence is shown, the account-type screen, or the Solo dashboard.

## Decision

Decided by the maintainer on 2026-11-18: fix the words only. No "Set up an organisation" link is added to the Solo
dashboard, so Solo keeps asking nothing about an organisation until the person chooses to look.

## Acceptance

- `tests/test_first_run.py::test_setup_page_points_to_settings_for_an_organisation` passes, and fails when the
  sentence is changed back to "from the dashboard".
- On a fresh install the first-run page shows the new sentence, and Settings, Organisation tab, shows the "Set up an
  organisation" card.
- No other template or test changes; the existing suite passes.
