# Skills authoring wizard

Status: shipped. Lane: `pillar:knowledge`.

Relates to:
- `docs/specs/knowledge-onboarding-and-guidance.md` (#683, shipped) - requirement 3 there claims a
  "native, in-app guided authoring wizard" shipped for Skills. This spec corrects that: what actually
  shipped was a single-page form with AI-assist buttons, not a step-gated wizard. This document is
  the spec that requirement should have pointed to.
- `docs/specs/knowledge-guidance-reconciliation.md` - the wider baseline-vs-advanced framing pass
  across Wiki/Memory/Snippets/Skills that this work shipped alongside.

## Problem

Founder, reviewing the live app: "the skill setup with the questionnaire needs to be a wizard
walkthrough and not a form that you fill out that provides the output below. This is not how a good
UI/UX workflow works."

Verified against the real template before building anything: `skills.html`'s "New skill" card
rendered every field simultaneously the instant "+ New skill" was clicked - name, scope, when-to-use,
description, instructions, governance-scope checkboxes, a "does it trigger?" tester, and a live
validation panel, all visible at once, with no `<button>`/step-gating, no `steps[]` array, nothing
hidden until an earlier field was answered. "Build by chatting" (an AI-assist option) and "Draft it"
made it guided in the sense that a question or two could fill the fields for you, but the authoring
SURFACE itself was not sequential - exactly the founder's complaint, not a taste difference.

## Requirements

1. THE SYSTEM SHALL present skill authoring as a sequence of discrete steps, each focused on one
   concern, with later steps hidden until reached:
   1. What should this skill do? (free text; triggers the existing AI draft)
   2. The basics (name, when-to-use, description - AI-prefilled where available, reviewed here)
   3. Instructions (the procedure the agent follows)
   4. Who can use it (sharing scope - personal/team/org - and governance scopes)
   5. Test it (the existing "does it trigger?" check against a sample prompt)
   6. Save (a summary of what will be saved, and the Save action)
2. A visible progress indicator SHALL show all steps, which one is current, and which are already
   completed, and SHALL let the user jump directly to any step (nothing here is a one-way gate - a
   user filling a skill by hand, or editing an existing one, must be free to work in any order).
3. A full summary/preview of the skill being authored SHALL appear ONLY on the final step, as a
   review immediately before committing - never rendered below an already-open, still-being-filled
   form (the shape of the founder's complaint).
4. Every existing capability SHALL be preserved with NO backend changes: the AI draft
   (`POST /skills/draft`), chat-refine (`POST /skills/refine`), local validation
   (`POST /skills/validate`), and the trigger check (`POST /skills/check-trigger`) are reused exactly
   as before. All fields SHALL remain part of the SAME `<form action="/skills/create">` with
   unchanged `name=` attributes, so `skills_create()`'s server-side contract - and every existing test
   that posts to it - needs no change.
5. A successful AI draft, or a chat-refine reply the model marks `ready`, SHALL auto-advance to step
   2 (reviewing what was drafted), so the wizard feels guided rather than static once a user engages
   the AI assist.
6. Editing an existing skill SHALL open the same wizard at step 2 (real content already exists - no
   reason to start at the blank "describe it" step), with every step still reachable via the
   indicator.

## Design note: why the progress indicator is not `_setup_steps.html`

`_setup_steps.html` (the account-creation onboarding stepper) is visually the right reference - the
same numbered-badge / done-checkmark / separator language - but it is a fixed 3-step,
SERVER-RENDERED, per-page indicator: each step is a separate route, and `setup_step` (an int passed
from the route) is evaluated once by Jinja at render time. This wizard is a single page with a
dynamic step count driven entirely by client-side JavaScript (no page reload between steps, fields
prefilled live from AI-assist responses). Reusing `_setup_steps.html` as-is is not possible without
either duplicating its markup per-step (defeating the point) or rewriting it into a JS-parameterized
component - which would touch the tested, working onboarding flow to serve a single new caller.
The chosen approach: a small, separately-named CSS block (`.skwiz-*`) in `skills.html`, matching
`_setup_steps.html`'s visual language deliberately (so the product feels consistent) without sharing
its server-rendering mechanism. If a THIRD caller needs the same dynamic-stepper pattern, promoting
`.skwiz-*` to the global stylesheet (the same move already made for `.adv-disclosure`, shared between
`_compute_chooser.html` and `wiki.html`) is the natural next step - not done here because a
second caller does not yet exist and premature extraction is its own cost.

## Out of scope

- The skills template gallery, local validation rules, and the trigger-check logic itself - all
  unchanged, per requirement 4 above.
- The AI-draft/chat-refine model prompts and behavior - unchanged.
- Wiki/Memory/Snippets' own baseline-vs-advanced framing - covered by
  `knowledge-guidance-reconciliation.md`, shipped alongside this in the same PR but a separate
  concern (Wiki/Memory/Snippets needed no step-wizard; only Skills' authoring flow did).

## Acceptance criteria

- Clicking "+ New skill" opens a wizard on step 1; only step 1's fields are visible.
- Clicking Next moves forward one step at a time; clicking a step in the indicator jumps directly to
  it; Back moves backward; the Back control is not shown/usable on step 1.
- A successful "Draft it" or a chat reply marked `ready` moves the wizard to step 2 automatically.
- The step-6 summary reflects the CURRENT values of every field, including ones entered after a
  jump-to-step-3-then-6 navigation (not stale AI-draft values).
- Clicking Save on step 6 submits the same `/skills/create` request a pre-wizard save would have,
  and the resulting skill appears on the Skills list exactly as before.
- Editing an existing skill opens the wizard at step 2 with its real fields already populated.
- Every pre-existing server-side skills test (`test_skill_scopes_authoring.py`,
  `test_skill_validation.py`, `test_skill_trigger_check.py`, etc.) passes unmodified.
