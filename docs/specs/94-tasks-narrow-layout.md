# Spec: Tasks work on narrow browser and PWA viewports

Status: implemented. Lane: `pillar:feature`. Issue: #94. Builds on `docs/specs/92-tasks-contrast.md` (the `tasks-page` wrapper).

## Problem

Below the desktop app's width the Tasks page kept a permanent 180px navigation column and a seven-column
table inside a card. Measured at 390 and 768 pixels wide, the workspace scrolled sideways by about 61
pixels, the navigation took 180 to 232 pixels beside the content, and the table's status, dates and action
buttons ended up past the right edge of the viewport.

## Requirements

- Below 880px (the Tauri window's configured minimum width, which is unchanged) the layout is compact:
  - The navigation rail becomes a bar across the top with the brand, the profile chip and a "Menu" button.
    The menu opens and closes from that button (`aria-expanded` and `aria-controls` are kept in step) and
    holds the same links and the account footer.
  - The content uses the full width, with a smaller padding.
- On the Tasks list (the table with class `tasks-list`, and only that table) the rows become one stacked card
  per task: the title and goal, then labelled lines for schedule (its lines stay separate: schedule, time zone,
  next run), scope, status, last run, runs and the action buttons. Buttons are at least 40px tall. The header
  row is visually hidden. Once the table is laid out as blocks, some screen readers (Safari with VoiceOver)
  drop the table semantics, so the labels are also repeated on each line as generated text. A table inside a
  task result (Markdown) is not affected and stays a table with its header row.
- The queue panel under a task is a full-width block with a stacked form (field above button).
- The Tasks create and edit dialog is inset 12px from each side and fits the viewport width. Other pages'
  dialogs (for example Agents) keep their own width.
- The Chat page lays the rail out as a flex row of its own, so below 880px it stacks the bar above the
  conversation. Without that the conversation is 0px wide.
- At 880px and wider the layout does not change: the rail stays a 232px column and the table stays a table (the
  colour and focus-ring changes of spec 92 apply at every width). The
  "Menu" button is not shown.
- The queue panel's toggle sets the row's display to empty (the stylesheet decides) instead of `table-row`, so
  it is a table row on the desktop and a block on narrow screens.
- No change to what the page does or to its wording. Other pages change only through the shared navigation
  bar below 880px (every page that uses the rail), which the walker test covers.

## Acceptance criteria

- At 390x844 and 768x1024 there is no sideways scrolling of the page or of the workspace (`.main`), on the
  Tasks list, with the queue panel open, after creating a task, and on the task result page.
- The navigation is a bar (full width, under 120px tall) with a Menu button that opens and closes it; the
  content is at least 95% of the viewport wide.
- Every task's title, status, dates and the Edit, Now, Queue and Cancel buttons are visible and inside the
  viewport. Result and Edit open the right pages and dialogs.
- The create dialog's card is inside the viewport; its fields and submit button can be reached, and a task
  can be created from it.
- At 880x600 and 1200x820 the rail is a column of about 232px beside the content, the Menu button is hidden,
  and nothing scrolls sideways. `src-tauri/tauri.conf.json` still has `minWidth` 880.

## Proof
- `tests/browser/test_rail_narrow_pages.py` walks every link in the rail, plus the Chat page, at 390, 768 and
  879 pixels and checks the content area is at least 90% of the width and the rail is a bar. It failed for
  `/chat` (content 0px wide) before the Chat fix. A second test checks the Chat conversation and message
  input at 390px.

- `tests/browser/test_tasks_narrow_layout.py` drives a real Chromium at the four sizes. It failed on main
  (workspace overflow of 61px, a 232px rail at 768px, action buttons past the edge) and passes after.
- A Markdown table in a task result keeps its header row and cells at both narrow widths; the Schedule cell
  reads as separate lines; the Agents create dialog is no wider than 560px at 768px.
- Contrast (#92) is unaffected: `tests/browser/test_tasks_contrast.py` still passes.
