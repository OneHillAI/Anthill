# Phase 1 of #683: per-surface interactive walkthroughs

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` (requirement 1) found that Wiki, Memory, Snippets,
and Skills are all built and sound, but nobody understands how or why to use them - the release gap
is guidance, not capability. This is the first of seven phases splitting that spec into independently
shippable PRs (see the phase plan referenced below); it ships the walkthrough mechanism on its own,
with no dependency on any later phase.

Verified against the real code before building: the existing tour (`anthill/web/static/tour.js`,
`User.onboarding_done`) is a single global, one-shot pass over the sidebar - there is no way today to
complete a Wiki walkthrough without also dismissing Memory's, and ending it early means it never
resumes for any surface. Wiki and Skills each have exactly one hover `?` popover; Memory has one prose
block; Snippets has none.

## What

- New `User.completed_walkthroughs` column: a CSV of surface keys (`wiki|memory|snippets|skills`) the
  user has completed or skipped, independent of `onboarding_done` and of each other.
- `GET /walkthroughs/status?surface=X` / `POST /walkthroughs/done` (Form: `surface`) - status/done
  round trip, validated against the fixed surface set.
- `anthill/web/static/walkthrough.js` - a generic step-through coachmark engine adapted from
  `tour.js`'s spotlight/card mechanics, but scoped to elements already on the current page rather than
  sidebar links, and keyed per-surface instead of one global pass.
- Each of Wiki, Memory, Snippets, Skills gets its own step sequence over its real, existing UI (no new
  affordances invented - a later phase's gallery/wizard/capture-pattern work gets its own walkthrough
  update when it ships) plus a "Take a tour" link to replay on demand.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 1 (self-explanation as interactive
walkthroughs). The spec's other five requirements are out of scope for this PR - tracked as later
phases of the same program.
