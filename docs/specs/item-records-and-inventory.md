# Spec: item records for sources and pages, and an inventory of existing wikis

Status: implemented. Lane: `pillar:knowledge`. Issue: #272, step 1 (slice 1a) of the knowledge base update, epic #270. Architecture:
`anthill-internal/knowledge-pillar/architecture.md`. Related: `docs/specs/keep-extracted-text.md`,
`docs/specs/index-extracted-text.md`.

## Problem

Nothing in a wiki records that an original, its kept text, its passages and the page written from it belong together, or what
state they are in. The only link is a provenance comment on the page, which is not a machine record. `Workspace.write_page`
rebuilds the page front matter from scratch on every save, so any field added to a page would be lost on the next save. Existing
wikis also hold leftovers nobody can see: originals and kept texts with no page, and the texts of uploads whose page was held or
rejected. Every later step of the update needs an identity and a state for each item, and a safe way to get existing wikis there.
This slice adds the record and the inventory. It changes no answer, no screen and no retrieval.

## Requirements

1. **The record** (`anthill/wiki/items.py`, `ItemRecord`): an immutable random id (a UUID, not derived from content or name);
   kind (`source` or `page`); space (today's scope: `personal`, `team`, `org`) and `space_ref` (the folder, such as `team-5`);
   `name` (a source's stored name, or a page's slug); origin (`own`, `model-written`, `person-edited`, `imported`, `unknown`);
   review state (`proposed`, `unchecked`, `checked`, `rejected`) and lifecycle state (`active`, `superseded`, `archived`,
   `trashed`, `purged`), kept as separate fields; a version and the content hash of the version it replaced; time learned; a
   content hash; and `derived_from`, the ids of the sources a page was made from (`name:<stored name>` when the original is not
   kept). An impossible value raises `RecordError`.
2. **Where it lives.** A page's record is the `x-anthill-item` key of its front matter. A source's record is a sidecar
   `records/sources/<stored name>.json`, not inside `raw/`, because the Files tab and `renovate` both read every file in `raw/`.
3. **A save keeps the record.** `Workspace.write_page` carries an existing page's record (id, origin, review state, lifecycle,
   links, learned), bumps the version and remembers the replaced hash when the body changed, and makes a record for a page that
   has none. A damaged record is not carried forward. Making a record never raises: on any failure the page is saved without one.
   `okf.OkfPage` carries the record through parsing and export.
4. **An upload keeps the record.** `ingest` makes the source record when it keeps an original ("proposed"). When a page made from
   it lands in the workspace the page's `derived_from` names it and the source becomes "checked". A page reaches a workspace
   through `write_page` whether it was written directly or approved from the review queue, so held and rejected uploads stay
   "proposed".
5. **The inventory** (`anthill/wiki/inventory.py`, command `anthill knowledge inventory`). Read only. Per wiki folder it reports
   counts (pages, originals, kept texts, whether a passage index exists) and: pages with no readable provenance, with a
   provenance comment that does not parse, or whose original is not kept; originals with no page, split by the status of the review
   row that proposed their page (pending, rejected) when review rows were read, or "no page and no review row, possibly a failed
   or timed-out upload"; kept texts with no original; duplicate originals (the same content hash in the stored name); and what
   `--apply` would add. The command covers every wiki on the install (`page_index.workspace_roots`), or the folders named with
   `--root`, and skips one that does not exist. `--json` prints the same as data.
6. **Review rows** are read from the database by the command (`cli._review_status_for`), matched to the folder they were proposed
   into (`user-<id>` by proposer, `team-<id>` by team, an organisation folder by organisation). The legacy personal wiki is not
   matched, and without a database nothing is claimed about held or rejected leftovers.
7. **Apply** (`--apply`). First a backup: `<parent>/_backups/<folder>-<stamp>.tar.gz` of `wiki/` and `records/`, the only places
   apply writes, read back to verify. If it cannot be made, nothing is written. Then a record for each page and original that
   has none: the new front matter key is appended after the existing front matter, which is kept byte for byte, and the body is
   not touched (a page with no front matter gets a minimal block in front of its unchanged text). A manifest
   `records/inventory-<stamp>.json` lists every change with the hashes before and after. Applying again with nothing to add writes
   no backup and no manifest. No model is called at any point.
8. **What apply can prove.** A page's origin comes from its provenance comment (a note filed as uploaded is `own`, a tagged page
   `model-written`, anything else `unknown`); its review state is `checked` for an own note or a page marked approved and
   `unchecked` otherwise; its version is 1, its time learned the page's own timestamp. An original's origin is `unknown` (an old
   original cannot be proved to be an upload); its review state is `checked` when a page names it, `rejected` or `checked` when a
   review row says so, and `proposed` otherwise.
9. **Undo** (`--undo <manifest>`). Restores each page from the backup only if it is still exactly as apply left it, removes the
   records apply made if unchanged, reports the rest as skipped, and renames the manifest `.undone`.
10. **Quarantine.** A page whose front matter does not parse, a record with an impossible value, a sidecar that does not parse, or a
    sidecar whose original is gone is listed as quarantined. It is never used, never overwritten and never repaired.
11. **The records live in the files.** Reading them (`items.list_items`) touches no database and no cache.

## Known limits

- Nothing reads a record's state yet: held, rejected and trashed items still reach answers until slice 1b.
- The inventory was run only on generated fixtures and on a generated 1,000 page wiki. It was not run on a real install.
- Matching a leftover to a review row depends on the page content in the row naming its original. A page with no readable
  provenance cannot be matched, and is reported as such, not guessed.
- Duplicate originals are found from the content hash in the stored name, without reading files, so an original stored under a
  name without that hash is not grouped.
- The passage index is reported as present or absent, not counted.
- The backup covers `wiki/` and `records/`. It does not copy originals or kept texts, which apply never writes.
- Existing model-written pages are recorded as `unchecked`. How a "to check" list is shown without listing every old page is for
  slice 1d.
- Skills, snippets, principles and memory items get no record here.
- A page copied as a file keeps its record, so two copies share an id. The one place that does this in code is the one-time
  migration of the legacy personal wiki into a user's own wiki. An id on a copy is settled in step 2.
- An OKGF export now carries each page's record (a random id, the folder name such as `user-1`, hashes). Importing a page
  discards the exporter's record and makes a new one. Whether an export should strip the record is for step 2 and the Node
  specification.
- Records appear gradually on an existing install, as pages are saved, until `--apply` is run. Nothing reads them yet, so a mixed
  state is harmless.

## Acceptance

- A page and a source get a record, and a save or a second `ensure` keeps it (`tests/test_item_records.py`).
- A page's origin and review state follow its provenance; a changed body bumps the version and keeps the replaced hash.
- A failure making a record never stops a page being saved.
- An upload through `ingest` and through the web route leaves a linked source and page record and no unrecorded item.
- The inventory names each kind of leftover on a fixture wiki, changes nothing on a dry run (a hash of the tree before and after is
  equal), and says so when review rows were not read (`tests/test_knowledge_inventory.py`).
- Apply writes a verified backup first, stops if it cannot, changes no page body byte (the text after the front matter is equal),
  keeps existing front matter, and is a no-op the second time.
- Undo restores the tree byte for byte, and leaves a page that was changed since.
- Damaged pages, records and sidecars are reported and untouched.
- Deleting `.cache/` loses no record. No model is called by the inventory, apply or undo.
- A generated 1,000 page wiki: dry run 0.04 s, apply with backup 0.65 s, reading every record 0.33 s, undo 0.22 s (one Mac).
