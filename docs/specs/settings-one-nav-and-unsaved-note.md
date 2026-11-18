# Settings: one navigation, and a note when a change is waiting

Lane: `pillar:platform`

## Problem

The Settings page (`/personalize`) listed its seven sections twice: as tabs across the top of the page and again
as a sub-list under "Settings" in the sidebar. The page also looks like it saves as you go, because its controls
are switches, but nothing is saved until the Save button at the bottom is pressed. Someone who flips a switch and
leaves loses the change without any sign. The Thinking row packed four ideas into one paragraph.

## Fix

1. **One navigation.** The sidebar sub-list is removed. The sections are the tabs on the page, and the sidebar
   has one "Settings" entry on every page.
2. **A note beside Save.** The Save bar shows "No unsaved changes", and "You have unsaved changes" in the warning
   colour as soon as any field or switch in the Settings form differs from how it loaded. It is a polite live
   region so a screen reader announces the change. Leaving the page with a change waiting asks first
   (`beforeunload`); submitting the form does not.
3. **The Thinking row** says what it does and that it only affects models running on this machine, in one
   sentence. The per-chat button is explained by the hint under the chat box, and the Web search default by the
   Privacy tab.

Not changed, and open for the owner: whether "Improve my model from my answers" starts on for a new account.

## Acceptance criteria

- The rail contains no `settings-subrail`, on the Settings page or any other.
- The tabs on the Settings page include all seven sections.
- The Save bar contains a `role="status"` note reading "No unsaved changes" that the script switches to "You have
  unsaved changes" and back.
- The Thinking row is one short description ending "Only affects models running on this machine."
- `tests/test_settings_unsaved_note.py`, `tests/test_nav_roles.py`.
