# Spec: layout and wording fixes for the answer panel, the Removed list and the edit page

Status: implemented. Lane: `pillar:knowledge`. Issue: #303. Follow-up to `docs/specs/answer-manifest.md` (#281),
`docs/specs/remove-an-item.md` (#278) and `docs/specs/edit-a-page.md` (#259). Source: the UI/UX session's review of the merged
screens on a running copy, with screenshots in light and dark at 1100 and 880 px wide. Nothing a person can do changes.

## Requirements

1. **Answer panel.** "What this answer was given" is its own full-width block under the answer's button row, on a saved answer
   (after the `.meta` row in `chat.html`) and on the answer as it arrives (a `.record-slot` created after the row). It was a
   child of a row that does not wrap, so it was about 165 px wide at 880 px. An older answer without a record keeps its two plain
   lines inside the row.
2. **Delete now** is a danger button (`btn-danger`) and asks with the app's own `data-confirm`, not the browser dialog. Restore
   is the ordinary button.
3. **Messages.** The message after Remove, Restore and Delete is shown above the Files list, with a gap below it, so it is in
   view when the page loads.
4. **Names.** The Files list, the Removed list and the confirmation page show the name the person uploaded (`original_name`);
   links keep the stored name. The confirmation for a page shows the page's title, and so does the list of pages that go or stay.
5. **Confirmation wording.** "The page made only from it, that nobody has checked, goes too: <title>" and "The page made from it
   stays. You can remove it separately: <title>", in the plural when there are several.
6. **Edit page.** Save is the primary button. "Your text replaces the page. The earlier text is not kept." is a visible notice. A
   line says the first heading, the line that starts with #, is the page's title.
7. **Page view.** "Edit this page" and "Remove this page" are one row of actions (`#page-actions`), shown to whoever may edit.

## Known limits

- How it looks is judged on a running copy; the tests check the markup. The UI/UX session did the review that led to this and can
  repeat it.
- A page whose heading is removed in the editor still falls back to its file name; the form says what the title is instead of
  stopping it.

## Acceptance

`tests/test_knowledge_screens_fixes.py` and the older tests that read these pages: the panel is a sibling after the button row
and not inside it, and an older answer is unchanged; the live page has its own slot; Delete now is a danger button with
`data-confirm` and Restore has neither; the message comes before the Files list in every case; names are the uploaded ones with
stored names in links; the confirmation shows titles and the plain wording; the edit page has the primary Save, the notice and the
title line; the page view has one action row for an editor and none for someone who cannot edit.

## Not verified here

Not looked at on a running copy: spacing, dark theme and mobile width of the changed screens.
