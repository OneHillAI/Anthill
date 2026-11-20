# Spec: an item needs a valid record to be used in an answer

Status: implemented. Lane: `pillar:knowledge`. Builds on `docs/specs/item-records-and-inventory.md` (the records and the
inventory) and `docs/specs/only-approved-items-reach-answers.md` (the rule for what an answer may read).

## Problem

An answer read a page or an original with no record, as it did before records existed. That made a record optional: whether an
item was approved, waiting or rejected could only be checked for items that happened to have one.

## Requirements

1. **One rule.** An item (a page or a kept original) is used in an answer only if it has a valid record whose lifecycle is
   `active` and whose review state is `checked` or `unchecked`. No record, or a record that cannot be read, means not used. A
   record that cannot be read is listed as quarantined (`items.list_items`) and is never repaired in place. `items.is_retrievable`
   returns False for no record; `items.page_file_retrievable` and `items.hidden_sources` follow it, and `hidden_sources` now also
   names an original in `raw/` that has no record.
2. **Every new item has a record.** Pages are written with one (`write_page`); originals get one when they are kept
   (`ensure_source_record`). A page or original that appears some other way (a file copied into the folder by hand) has none and is
   not used until it is given one.
3. **A wiki from before records is prepared once, automatically.** The first time a wiki is read in a process, and at app start
   for every wiki on the install, `inventory.ensure_upgraded` gives each page and original without a record one, using the
   inventory's apply: a backup first, no page text changed, undone byte for byte with `anthill knowledge inventory --undo`.
   A marker (`records/.upgraded.json`) means it is not repeated. At app start the review rows are used, so an upload that is
   waiting stays out of answers and a rejected one stays rejected; with no review rows (a command line or library caller) an
   original with no evidence either way was readable before and stays readable (`checked`). Pages and originals the inventory
   skips (damaged) stay quarantined.
4. **Asking while it runs is safe.** The upgrade holds a lock: a question that arrives while a wiki is being prepared waits for it
   and then sees the prepared wiki. It is never answered from a half-prepared wiki.
5. **If it cannot run** (a read-only disk, a backup that cannot be verified) the reason is kept, the wiki's items without a record
   stay out of answers, and the wiki shows one line at the top: "Anthill could not prepare this wiki, so pages and documents
   made before this version are not used in answers. Reason: ... It will try again at the next start." The next start tries again.
6. **The person is told once afterwards**, when the upgrade changed something: "Anthill prepared this wiki: N pages and M documents
   now have records, and no page text changed. A backup was made first. To undo it, run: anthill knowledge inventory --undo
   records/<manifest>". It is shown once.
7. **A user who does nothing** gets the same answers as before the upgrade for everything that was readable, and is asked nothing.

## Known limits

- The upgrade rewrites each page's front matter, so a cached answer that cited a page is judged out of date once and is not reused.
- A page edited by hand outside the app so that it loses its front matter has no record and is not used until the next
  upgrade run gives it one (the inventory command, `anthill knowledge inventory --apply`).
- Two separate processes (the app and a command line) preparing the same wiki at the same moment are not coordinated; the marker and
  the backup make a repeat harmless, not impossible.
- With no review rows, an upload that was waiting or rejected before this upgrade is read as before; the app, which has the rows,
  does not.
- Measured on 1,000 pages and 50 originals on one Mac: the first read, including the upgrade and its backup, took about 610 ms;
  later reads about 7 ms.

## Acceptance

`tests/test_records_required.py`: no record is not allowed; a page and an original made after the upgrade without a record are
not used and what was prepared still is; a hand-broken record is quarantined, not used and not repaired; every way of making an
item gives it a record; a legacy wiki loses nothing and no page text changes; the first question on a legacy wiki is answered from
its pages and documents; it runs once; it is undone byte for byte; a waiting or rejected upload stays out when the review rows say
so; eight questions at once all see the pages; when it cannot run old items stay out, the notice says why and the next start
succeeds; the person is told once; a wiki with nothing to prepare says nothing and changes nothing; a folder that is not a
workspace is left alone. Older tests that assumed an item with no record is used were changed to build their wiki the way the app
does.

## Not verified here

Not run on a real install with real pages. The notice's look has not been seen on screen.
