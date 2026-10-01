# Chat: remove the duplicate header Rename/Delete buttons; fix the sidebar's overlapping icons

## Problem

A conversation had the same two actions in two places:

1. `chat.html`'s own header (top right of the open chat) had `Rename` and `Delete` buttons, each a
   full `<form>` posting to `/chat/{id}/rename` and `/chat/{id}/delete`.
2. `_sidebar.html`'s per-conversation row has its own pin / rename / delete icon group
   (`.rail-conv-actions`), revealed on hover, posting to the exact same two routes (plus pin).

Only (2) needs to exist - it works for every conversation in the list, not just the one currently
open, and is already how a conversation is renamed/deleted/pinned from anywhere else in the rail.

Separately, (2) had a real layout bug on the packaged desktop app (founder report, screenshot): the
three icons (pin, rename, delete) overlapped the conversation's title text and looked misaligned
instead of sitting cleanly after it. `a.rail-conv` reserved `padding-right: 78px` for the icon group,
sized only by each `.rail-conv-del-btn`'s `padding: 2px 5px` around a 12px icon - with no
`-webkit-appearance: none` on the buttons, so in the desktop shell's WKWebView a `<button>` can keep
native pushbutton sizing behavior alongside the explicit CSS, making the three buttons wider in
practice than the padding-based math implied and eating into the 78px the title text was relying on
to stay clear of them.

## Fix

1. **Remove the duplicate header buttons.** `chat.html`'s `Rename`/`Delete` `<form>`s are gone; the
   sidebar row is the one and only place to rename, delete, or pin a conversation. No backend route
   changed - the sidebar already posts to the same `/chat/{id}/rename` and `/chat/{id}/delete`.
2. **Fix the icon sizing/overlap.** `.rail-conv-del-btn` now has `-webkit-appearance: none;
   appearance: none; box-sizing: border-box;` and a fixed `width: 24px; height: 24px` (icon centered
   with flex) instead of padding-driven sizing - so pin/rename/delete render at exactly the same size
   regardless of any native button chrome the engine might otherwise apply, and `a.rail-conv`'s
   reserved space goes from `78px` to `92px` to give the now-precisely-known icon-group width
   (3 x 24px + 2 x 2px gap = 76px, plus the group's own 6px right offset = 82px) a real margin instead
   of a razor-thin one.

## Acceptance criteria

- Opening a conversation shows no `Rename`/`Delete` buttons in the chat header; the folder selector
  and (when there are messages) "Turn into a skill" remain.
- Hovering a conversation row in the sidebar shows pin, rename, and delete as three identically-sized
  (24x24px) buttons, evenly spaced, with no overlap with the conversation title at any title length
  (verified via rendered `getBoundingClientRect()`: title text's available width ends with a visible
  gap before the icon group starts).
- Renaming, deleting, and pinning a conversation from the sidebar still work (same routes, untouched).
- The folder-row delete button (`.rail-folder-summary .rail-conv-del-btn`) keeps its own hover-reveal
  behavior and is unaffected in size/position.
