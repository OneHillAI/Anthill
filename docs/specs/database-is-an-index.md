# Spec: what the knowledge database holds can be made again from the files

Status: proposed. Lane: `pillar:knowledge`. Builds on `docs/specs/item-records-and-inventory.md` (records are in the files),
`docs/specs/one-page-write.md` (the earlier versions of a page are files), and `docs/specs/remove-an-item.md` (removed items are files).

## Problem

Pages, originals, their records, the earlier versions of a page and the removed items are files. A change proposed for review
(a held upload, an imported bundle, a page written by an agent) is only a row in the database, so deleting the database loses every
proposal that is waiting for a decision. The test named for rebuilding deletes `.cache`, a folder of indexes, and no test deletes the
database itself.

## Requirements

1. **A pending proposal is also a file.** When a proposal for a wiki is committed to the database as pending, a file
   `proposals/<uid>.json` is written beside that wiki (a temporary file, then a rename). It holds what the row holds apart from its
   own id, and the row gets the same `uid`. When the proposal is decided (approved or rejected) or its row is deleted, the file is
   removed. A transaction that does not commit leaves no file. A file that cannot be written does not undo the proposal.
2. **The proposals come back.** At start-up, a pending proposal found as a file whose `uid` the database does not have is added again,
   as it was. A file that cannot be read, is not pending, or lacks a field is left alone. A proposal whose organisation or team no
   longer exists is left in its file. If its proposer's account is gone, a proposal for a personal wiki is attributed to the owner of
   that wiki and any other has no proposer.
3. **Everything else is read from the files at start-up.** The registry of pages is made again from the pages, the passage index from
   the kept texts, the page vectors when a question needs them, and the records (and their upgrade for a wiki from before records)
   from the files.
4. **The check.** A test builds a wiki with a document and its page, a page edited twice (so it has versions), an approved page, a note
   and a removed document, leaves two proposals pending, approves a third, and edits a page in a text editor. It deletes the database
   file and its journal files, deletes the `.cache` folder, makes a new empty database with the same accounts, and runs the start-up
   passes that read the files. Every file of the wiki is as it was, the pages, sources, versions, states and links are the same, the
   registry and the passage index are made again and an answer reads the document, the two pending proposals are back (and the decided
   one is not), the outside edit is still noticed, and a restored proposal can be approved.
5. A person who deletes nothing sees nothing new except a `proposals` folder while something waits for review.

## Not changed

Accounts, teams, settings, chats, answer records and audit rows stay in the database: they are not knowledge. A proposal that was
decided is not kept as a file, so a rebuilt database has no history of decisions. Which proposals are needed, who may approve them and
what they are reviewed for do not change.

## Known limits

- The test runs the start-up passes that read the files, not the whole start-up (the scheduler and the network parts are not run).
- A proposal for a wiki folder that does not exist yet has no file until that folder is made.
- The accounts must exist again (the same organisation and users) for a proposal to get its proposer back.

## Acceptance

In `tests/test_database_is_an_index.py`: the rebuild above; a proposal put back from its file can be approved, and approving removes
its file; a decided or rolled-back proposal leaves no file; unreadable files and a missing proposer account are handled as in point 2.
