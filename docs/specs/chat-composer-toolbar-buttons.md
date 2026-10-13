# Chat: one row of buttons under the message box, and a leaner top bar

## Problem

The strip under the chat message box held an `Options` button that opened a hidden panel with a Web
search checkbox and a knowledge scope dropdown, next to a separate `Thinking off/on` button. Two
controls of the same kind looked different (a button and a checkbox), and the two that change what a
question reaches (the web and which wikis) were one click deeper than the one that changes speed.

The chat top bar also carried a "Project" label and a "Create a project" link beside the project
dropdown. The label repeats what the dropdown already says, and creating a project is done from the
Projects page.

## Fix

1. The `Options` button and its panel are removed. In their place, one row of three buttons in the same
   style: `Web search off/on` (click to flip it), `All knowledge` (click to choose which wikis the
   answer may use), and the existing `Thinking off/on`.
2. Web search keeps its hidden checkbox (`#opt-web`) as the single source of truth for the send code and
   the per-chat saved choice. The button only flips it and repaints its own label.
3. The knowledge button is a label with a transparent native select laid over it, so a click opens the
   system picker and keyboard and screen readers work unchanged. Its text shows the chosen scope.
4. The first-use notice no longer points at an "Options button".
5. The top bar keeps the "Running on this machine" line and the project dropdown. The "Project" label
   and the "Create a project" link are removed.

## Acceptance criteria

- There is no `Options` button or options panel in the chat page.
- `Web search`, the knowledge scope and `Thinking` are three visible buttons of the same height and style
  on one row.
- Clicking `Web search` flips it for this chat, updates the top-bar trust line, and is remembered for
  this chat; a chat with no choice of its own still follows the account default.
- Choosing a scope from the knowledge button changes its label, and the next message is sent with that
  scope.
- The top bar shows no "Project" label and no "Create a project" link; the project dropdown still moves
  the chat into a project.
