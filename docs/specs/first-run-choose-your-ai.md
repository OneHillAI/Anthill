# First run: one decision at a time when choosing your AI

Lane: `pillar:platform`

## Problem

The first-run "Choose your AI" page asked three things at once: where the model runs (two cards with the unexplained
labels "Graduate" and "Professional"), which model (a long list with several "recommended" entries, a "Lead" chip,
and a warning about a "council" on a machine that could never run one), and which inference provider. The primary
button said "Continue with this council" even for a single model. A first-time user chose without understanding
(issue #154). The page also let section outlines overlap, listed models that are not chat models (an embedding
model), and ended with a loose "Frontier models" line.

## Fix

First run only. Settings keeps the layout in `docs/specs/ai-stack-guided-layout.md`.

1. **Where it runs is a statement.** The page says "Your model runs on this computer" with the hardware line, that
   it is private and works offline, and that a bigger model in the user's own cloud can be set up later in Settings.
   The two tier cards stay in the page, hidden, because the shared script addresses them.
2. **The best fit is chosen for the user.** The model list is headed "Choose your model". The app's own default for
   this machine is checked, listed first and the only one labelled "Best fit for this machine". Every other model
   stays selectable, the origin filter (All, US, EU, China, Other) is unchanged, and the list is no longer a
   nested scroll region. This replaces the 2026-09-28 rule that nothing is preselected.
3. **No council on a machine that cannot run one.** Below the council memory floor the "Council needs..." warning is
   not shown and the "Lead" chip never appears for a single model. On a machine at or above the floor, picking a
   second model shows "Optional: add a second model so two models check each other." and the chip appears once two
   models are picked.
4. **The button names the choice.** "Continue with Qwen3.5 9B" for one model, "Continue with these 2 models" for
   several, "Choose a model to continue" while none is picked. It no longer submits: it opens step 4.
5. **Embedding-only models are not offered** as a chat model (installed tags that start with `bge-` or contain
   `embed`).
6. **The provider is step 4, its own screen.** After the model, the second pane of the same form asks "Want faster,
   stronger answers on hard questions?", says what stays private (wiki, files and memory stay on this computer; what
   leaves is shown with the provider's name before it is sent, once or always; the provider can be disconnected in
   Settings), shows the existing provider cards, and offers "Connect and finish", "Not now" and "Back to your
   model". Nothing is saved until the form is submitted, so one submit still saves both choices. The progress bar
   has four steps and moves to step 4 without a page load. The "Frontier models" line is removed from the page.
7. The link beside each provider reads "How open is it?" (it was "Ownership grade").
8. **Step 4 has one way to skip, and it is announced.** The footer "Skip for now" link belongs to step 3 and drops the
   chosen model, so it is hidden while step 4 shows (step 4 has its own "Not now", which keeps the model); it
   returns on "Back to your model". Opening step 4 moves keyboard focus to its heading, and going back moves it to
   the model pane, so a screen reader announces the change. The progress bar marks the current step with
   `aria-current="step"` on every setup page, and the script moves the mark between steps 3 and 4.

## Acceptance criteria

- `/setup/model` has no `Graduate`, `Professional`, `Which model do you run?`, `Council needs` or
  `Continue with this council`, and no numbered section headings.
- Exactly one model row is checked and carries "Best fit for this machine", and it is first; the other rows are
  unchecked and selectable.
- Embedding-only installed models are not rows on that page.
- The primary button opens the provider pane and the four-step bar marks step 4 current; "Not now" submits with no
  provider and lands on the dashboard.
- With step 4 showing, the footer "Skip for now" form is hidden; with step 3 showing it is visible.
- Opening step 4 focuses `#setup-provider-heading`; "Back to your model" focuses `#setup-pane-model`.
- Exactly one step carries `aria-current="step"`.
- `/personalize` (Settings) is unchanged.
- `tests/test_setup_model_council.py`, `tests/test_ai_stack_layout.py`, `tests/test_model_picker_onboarding.py`.
