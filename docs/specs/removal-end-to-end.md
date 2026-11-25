# Spec: removing a document or a page clears what was made from it, and a person's page says its source is gone

Status: proposed. Lane: `pillar:knowledge`. Builds on `docs/specs/remove-an-item.md` (remove, restore and delete with 30 days of grace),
`docs/specs/one-eligibility-check.md` (one rule decides what may be read) and `docs/specs/one-page-write.md` (the kept earlier versions of
a page).

## Problem

Removing an item moves its files out of the folders readers scan, and the reading routes follow one rule, so a removed item is not
given to a model. Some copies made from it stay on disk until the item is deleted for good after 30 days: the vector of a removed
page, the answers saved from earlier questions, and the earlier versions of a removed page, which hold its old text. A page that a
person edited or approved stays when its document is removed, and nothing on the page says its document is gone.

## Requirements

1. **Copies are cleared when the item is removed.** Removing a page or a document, at once and not after the grace period, takes out
   the page's search-index entry, its page vector and the answers saved in the workspace. The passages of a removed document were
   already taken out. Restoring puts the files back and the pages and passages are indexed again; saved answers are made again by the
   next question.
2. **The earlier versions go with the page.** The files in `history/<slug>/` of a removed page (and of each page that goes with a
   removed document) move into the removed-items folder with it, come back with a restore, and are deleted with the item.
3. **A page that stays says so.** A page that names the document it was made from, when that document is not kept, shows "Source
   removed" in the Library, and on the page, on a line of its own that names the document and says why the page stays, with a link to
   the list of removed items where the document can be restored. Nothing is stored for this: restoring the document clears it, and a page that never had a
   document is never tagged. The page is still used in answers (its record is unchanged).
4. **The check.** A test ingests two documents, one with a phrase nothing else contains, asks about it, removes it, and asks again. The
   phrase is looked for in what the model is given, in the answers, in the results of the tools, and in every file of the wiki folder
   outside the removed-items folder; then the document is restored and the phrase comes back. The routes asked are the blocking
   answer (chat, widget, Slack), the streaming answer, the MCP wiki tool, the agent tools (agent mode and tasks) and the
   command-line document command. The test is run with the answer cache off and with it on.

## Not changed

- Which items an answer may read, and how a removal is confirmed, restored or deleted.
- A person who removes nothing sees nothing new.

## Known limits

- What a delete cannot reach is as in `remove-an-item.md`: backups, exports, copies made elsewhere, the database's own journal files
  and anything a model was trained on.
- A saved chat still names a removed item as text in that chat; earlier answers that used it are not replayed into new prompts.
- The routes in point 4 are the ones the test asks. A route that is not named there is not claimed.

## Acceptance

In `tests/test_erasure_canary.py`:

- Before removal the phrase reaches the model, the answers and the tools on each named route; after removal it appears in none of them
  and in no file outside the removed-items folder; after a restore it is back on each route; the other document is unaffected.
- With the answer cache on, an answer saved before the removal is not served and its text is not left in the cache's files.
- The page vector of a removed page is gone at once.
- The earlier versions of a removed page are not in the wiki folder after the removal and are back after a restore.
- A page a person approved stays, is tagged in the Library and on its page, still counts as readable, and loses the tag when the
  document is restored; a page with no document and a page whose document is kept are never tagged.
