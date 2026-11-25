# Spec: one place a page is written, whole, with its earlier version kept

Status: proposed. Lane: `pillar:knowledge`. Builds on `docs/specs/item-records-and-inventory.md` (the record in a page's front
matter) and `docs/specs/edit-a-page.md` (a person corrects a page from its page view).

## Problem

A page is a file in `wiki/`. Today it is written by replacing the file in place, so a crash or a full disk can leave half a page. The
edit screen saves whatever it is given, so a second save made from an older copy replaces the newer text without a word. The text a
save replaces is not kept. A page changed in a text editor outside the app is overwritten by the next write, and the content hash in
its record is never compared with the file. If making the record fails, the page is saved without one.

## Requirements

1. **One write function.** `Workspace.write_page` is the one place a page file is written. Every caller that writes a page goes through it.
2. **Whole or not at all.** The page is written to a temporary file in the same folder and renamed over the old one. A failure leaves
   the old page as it was and no temporary file behind.
3. **The earlier version is kept.** Before a write replaces a page whose text changes, the whole previous file (record included) is
   kept as `history/<slug>/<number>-<hash>.md`, numbered in order. A write that does not change the text keeps nothing. History is not
   a page: it is not listed, indexed or read by an answer.
4. **A save from an old copy is refused.** A caller that read the page first passes the hash of the text it read (`base_hash`, the
   same hash the record holds). If the page is not that text when the write runs, nothing is written and `StaleSave` is raised. Writes
   to one page take turns, so of several saves made from the same copy one is applied and the others are refused.
5. **An edit made outside the app is noticed.** When a page file's text does not match the hash in its record, it was changed outside
   the app. The record is brought up to date as a person's edit (a new version, origin `person-edited`, checked) and the text is not
   touched. Anything but a person's save made from that text is then refused (`StaleSave`, marked as outside), so a model, an import
   or a task does not replace it. A page with no record, or a damaged one, is not judged this way.
6. **A record is always written.** If the usual record cannot be made, a plain one is written (new id, origin unknown or
   person-edited, unchecked or checked). A page is never saved without a record.
7. **The edit screen.** The form carries the hash of the page it was opened on. A save made after the page changed returns the form
   with the person's text still in the box and a message that says whether the page was changed by someone else or outside Anthill.
   The notice on the form says the earlier text is kept.
8. **Callers.** A model-written change to a page that was changed outside the app waits in the review queue, flagged "page-changed",
   instead of being applied. Approving a queued change, regenerating pages with a new model and filing an ingested document leave a page
   changed outside the app as it is.

## Not changed

- What an answer may read. Retrieval still follows the record's lifecycle and review state.
- Which writes need a person's approval. That decision stays where it is (`propose_wiki_write`).
- The page history is kept for a person to open and restore in a later change; this one only keeps it.

## Known limits

- Writes take turns inside one running app. Two separate app processes writing the same wiki folder are not coordinated.
- History grows with every changed write and is not trimmed.
- A queued change is applied as written when it is approved; it is not compared with edits made to the page after it was proposed.
- A change made outside the app to the front matter of a page, not its text, is not noticed here.

## Acceptance

Behaviour, tested in `tests/test_one_page_write.py`:

- A write that fails part way leaves the old page whole and no temporary file.
- Each write keeps the text it replaced; writing the same text again keeps nothing.
- A save made from an old copy is refused and the page keeps the newer text; of twelve saves at once from one copy, one is applied and
  eleven are refused.
- An edit made in a text editor is recorded as a person's edit and survives a model write, an import and an approved proposal; a
  person's save made from that text replaces it and it stays in history; a save made from an older copy is refused.
- A page whose record cannot be made, or is broken, still has a valid record after the write.
- In the edit screen a second save from the same start is refused with a message and the person's text stays in the box.
