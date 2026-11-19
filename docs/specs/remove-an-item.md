# Spec: remove a document or a page, with a way back for 30 days

Status: implemented. Lane: `pillar:knowledge`. Issue: #278, step 1 of the knowledge base update, epic #270. Builds on
`docs/specs/item-records-and-inventory.md` (records) and `docs/specs/only-approved-items-reach-answers.md` (what answers may read).
Architecture: `anthill-internal/knowledge-pillar/architecture.md`.

## Problem

Nothing a person uploaded can be removed. No route deletes a page, an original or its kept text, so a wrong or unwanted document
stays in the wiki, the Files tab and the search indexes for good.

## Requirements

1. **Remove moves, it does not delete.** Removing a page moves `wiki/<slug>.md`. Removing an original moves `raw/<name>`, its kept
   text `text/<name>.txt`, its record `records/sources/<name>.json` and the pages it takes along (point 2). Everything goes into
   `<workspace>/.trash/<id>/`, in the same layout, with a small manifest (`removal.json`: what, when, which files). `.trash/` is not
   a folder any reader scans, so a removed item cannot reach an answer, a tool or a search whatever a read path remembers to check.
   The move is a rename on one disk and is undone file by file if it fails half way.
2. **What goes with an original.** A page goes with its original only when a model wrote it from that original and no person has
   checked or edited it (record: origin `model-written`, review state `unchecked`, lifecycle `active`). A page a person approved or
   edited, and a page with no record, stays. Removing a page never removes its original. The confirmation lists both groups before
   anything moves.
3. **Restore.** Moves every file back, re-indexes the text and the pages, rebuilds `index.md`, and logs it. If a file with the same
   name has been made since, it refuses, says so, and moves nothing. The workspace is then byte for byte what it was.
4. **Delete now, and the 30 days.** Delete removes the bundle and everything derived from it: the page vector rows, the passage rows
   of the kept text, the database registry row, the line in `index.md`; and writes a line to `log.md`. Anything removed more than 30
   days ago is deleted the same way when the app starts and when the Files tab opens. There is no scheduler and no setting. A delete
   that was interrupted is marked in the manifest, can no longer be restored, and is finished at the next expiry.
5. **Who.** Same permission as editing that wiki: your own wiki, a team owner, an organisation admin. A Solo account has no
   organisation wiki. The routes are `GET /wiki/remove` (the confirmation), `POST /wiki/remove`, `POST /wiki/removed/{id}/restore` and
   `POST /wiki/removed/{id}/delete`. An id is twelve hex characters or it is refused.
6. **Where.** A "Remove" link on each original in the Files tab and "Remove this page" on a page; a "Removed" list in the Files tab
   with the days left, Restore and Delete now. The list appears only when something is removed.
7. **A person who removes nothing sees nothing new.** No folder is made, no file is written, nothing is asked. No model is called.

## Known limits

- **What a delete cannot reach**, and the confirmation says so: backups of the data folder, exports, copies made elsewhere, the
  database's own journal files, and anything a model has already been trained on. Delete removes the item from Anthill; it is not
  a way to take knowledge out of a model.
- A search engine index run next to Anthill (Meilisearch) may still hold an entry for a removed page; the entry is mapped to pages
  through the list of pages that can be read, so it is never used.
- A removed item that a saved chat names is still named in that chat as text. The chat records are not rewritten.
- The cross-node answer index that an organisation can run (`central_index`) is not covered here.
- Replacing a document with a newer one, archiving, renaming, deleting a project's folder and any screen that lists all items are
  separate steps of the epic and are not built here.

## Acceptance

All in `tests/test_remove_an_item.py`:

- A page moves out of the wiki and its original stays; an original takes its text, its record and only the pages a model made
  unchecked; the plan names what goes and what stays; names that are not there, or look like paths, are refused.
- Remove then restore returns the workspace byte for byte and the document is searchable again; a restore whose name is taken refuses
  and moves nothing; an id that is not twelve hex characters is refused.
- Delete leaves nothing derived behind (vector rows, passage rows) and keeps the other documents; removed 31 days ago is deleted and 29
  days ago is not, with the days left shown; an interrupted delete is finished; every wiki on the install is expired; a damaged
  manifest is left alone; no model is called.
- A removed document and page never reach a prompt and come back with a restore.
- Through the real web app: the confirmation text (what goes, what stays, 30 days, what a delete cannot reach); remove, restore and
  delete through the pages; a restore that cannot be done says why; the remove link on a page; the registry row goes; the permission
  matrix for own wiki, team owner, team member, an admin outside the team, org member, org admin and a Solo account; a person who
  removes nothing sees no list and no `.trash/`.

## Not verified here

Nothing has been run on a real install or in a browser, only through the test client. A very large original (a rename on another
disk is a copy) and a data folder on a network drive are not measured.
