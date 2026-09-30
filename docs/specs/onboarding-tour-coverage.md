# Onboarding tour: cover every primary surface

## Problem

The in-app "Take a tour" (`anthill/web/static/tour.js`) is a coachmark walk-through that auto-runs once
for a new user, spotlighting sidebar items one at a time. It had 7 steps - Chat, Snippets, Memory, Skills,
Teams, Metrics, Personalize - and skipped the **Tasks** and **Agents** surfaces (the two biggest recent
additions), plus **Wiki** and **Integrations**. A new user could finish onboarding without ever being
shown Tasks or Agents (#390). Two steps were also stale: the Chat step described an "agent mode toggle"
that #421 retired, and the Teams step used the old "Teams" label after the sidebar moved to "Projects".

## Requirements

- The tour has a step for every primary sidebar surface, each targeting the current sidebar `href` and
  described in one line consistent with the "How it works" page:
  Chat, Tasks, Agents, Snippets, Memory, Wiki, Skills, Projects, Integrations, Metrics, Personalize.
- Step order follows the sidebar top-to-bottom so the spotlight moves naturally: interaction (Chat, Tasks,
  Agents), then knowledge (Snippets, Memory, Wiki, Skills), then collaboration (Projects, Integrations),
  then the payoff (Metrics), then Personalize last (it keeps its "Personalize now" call to action).
- Copy stays current: the Chat step reflects that the router decides depth (no manual agent toggle; "go
  deeper" in words); the Projects step matches the sidebar label.
- The tour keeps degrading gracefully: a step whose target is absent for this user is skipped
  automatically (unchanged behaviour), so a surface hidden for a role or plane never breaks the walk.

## Acceptance criteria

- `tour.js` lists steps for `/chat`, `/tasks`, `/agents`, `/snippets`, `/memory`, `/wiki`, `/skills`,
  `/teams`, `/connectors/mcp`, `/metrics`, `/personalize`, in that order.
- Every step selector matches a link present in `_sidebar.html`.
- The Chat step no longer mentions an "agent mode" toggle; the Teams step reads "Projects".
- Running the tour spotlights the correct sidebar item at each step (verified: the Agents step highlights
  the `/agents` link).

## Scope

Content of the tour's `STEPS` array only. The tour engine (spotlight, card, keyboard nav, skip/complete,
server onboarding flags) is unchanged.
