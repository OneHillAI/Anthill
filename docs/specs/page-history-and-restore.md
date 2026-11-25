# Spec: see a page's earlier versions and restore one, and a clear message when a save is refused

Status: proposed. Lane: `pillar:knowledge`. Builds on `docs/specs/one-page-write.md` (the earlier versions of a page are kept and a save
made from an old copy is refused) and `docs/specs/edit-a-page.md`.

## Problem

The earlier versions of a page are kept, but there is no way to see them or to go back to one. When a save is refused because the page
changed, the message says so but gives no way to look at what the page says now or at what it was.

## Requirements

1. **History.** A page that has earlier versions shows a "History" link next to Edit and Remove. The history page lists the page as it
   is now and each earlier version, newest first, with the day and time it was written, who wrote it (an AI summary, a note as uploaded, a person's edit, or a restore
   of an earlier version) and one short line on what changed from the version before (lines added and removed). The newest row is
   labelled "(now)". Whoever may read the page may read its history.
2. **One earlier version.** Opening a version shows its text as it was and how it differs from the page now (the changed
   words marked, as in the comparison for a newer upload), and says which row of the list it is. Who may edit the page sees "Restore this version" there.
3. **Restore.** Restoring writes the old text as a new version of the page, by the person who restored it, through the one write
   function: the text it replaces is kept in history too, a restore made from a page that has since changed is refused like any save, and
   nothing is erased. The page keeps its record and its source. The version made says it was restored from version N, and the page then says so
   ("Restored version N. The page as it was before is kept in History.").
4. **The conflict message.** When a save from the edit screen is refused, the screen keeps the person's text in the box, shows one alert (the page changed after it was opened, or was changed
   outside Anthill) with links to the page as it is now and to its history, and the button says "Save anyway".
5. A person who never opens the history sees nothing new but the link.

## Not changed

Who may edit a page, what a page is used for in answers, and the one write function. Removing a page does not change how its history is kept.

## Known limits

- Versions are shown whole; there is no line-by-line comparison.
- Only the newest 50 earlier versions of a page are kept (`docs/specs/one-page-write.md`), so the list is at most that long.

## Acceptance

In `tests/test_page_history.py`: the link appears only when there is history; the list shows the current page and each earlier version
with who wrote it; a version opens read-only with its text; restoring makes a new version with the old text, keeps the text it replaced
in history, keeps the page's record and source, and is refused when the page changed since the history page was opened; a person who
cannot edit sees the history but no restore button and cannot restore; a refused save in the edit screen links to the page and its
history; a page of another person's wiki has no history page for someone who cannot read it.
