# Accessibility basics: skip link, main region, labelled chat fields

Lane: `pillar:platform`

## Problem

A review of the running app found that a keyboard user had no way past the menu to the content, the chat page
(its own document, not built on `base.html`) had no main region for a screen reader to jump to, and several chat
fields had no label, so a screen reader announced them as unnamed edit boxes. Some sidebar labels were drawn at
10 px.

## Fix

1. **A skip link** ("Skip to main content") is the first focusable element on every page built on `base.html` and
   on the chat page. It is off screen until it has focus, then appears at the top left with the accent border.
   It points to `#main-content`.
2. **A main region on chat.** The chat column carries `role="main"` and `id="main-content"`. Other pages already
   had `<main id="main-content">`.
3. **Labels on the chat fields**: the message box ("Message"), the image picker ("Attach an image"), the snippet
   tags box, the canvas change request, and the "When to run" select on a recurring-task card.
4. **No sidebar label below 12 px**: the conversation section heading and the two profile-menu labels move from
   10 px to 12 px.

The task dialog already uses `<dialog>` with `role="dialog"`, `aria-modal`, a labelled title and an Escape handler,
so issue #87's dialog item needs no change.

## Acceptance criteria

- The first link in the body of chat, tasks and the dashboard is the skip link, and `#main-content` exists once and
  is a `<main>` or has `role="main"`.
- The skip link is positioned off screen and moves to the top on `:focus`.
- Each field above has an `aria-label` (or a `<label for>`).
- `tests/test_accessibility_basics.py`.

Not covered here, and left for a follow-up: forms on Agents, Wiki and Settings where a field sits next to its text
instead of inside a `<label>`.
