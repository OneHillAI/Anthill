# Spec: an upload never duplicates or overwrites a wiki page without the user's say

Status: implemented. Lane: `pillar:knowledge`. Issue: #149.

## Problem

Uploading the same file twice made two pages (#149). The model titles a note with no heading differently on each
run, so the second upload landed on a new slug while the raw file, stored once under its content hash, was shared
by both pages. A note with a heading is titled after it (#150), so there the second upload had the same slug and
silently overwrote the first, and two different files with the same heading overwrote each other. Either way the
user's knowledge changed without a word.

## Requirements

1. Before an uploaded or imported page is filed, it is compared with the wiki it is going to (personal, team or
   org). It is a conflict when either holds:
   - `same_file`: an existing page was ingested from the same source hash (the 12-hex hash in the page's
     provenance `source`, which ingest derives from the file's sha256), whatever the file was called and
     whatever title the model gave it;
   - otherwise, a page already exists at the new page's slug: `new_version` when it came from a file with the
     same name (hash aside), `title_clash` when it came from anything else.
2. A conflicting page is never written, replaced or duplicated by Anthill. It becomes a pending review (the
   existing queue), under the existing page's slug, flagged with the conflict code, with a proposal from Anthill
   at the top of the review text: what it matched, when that page was added, and a recommendation - keep the
   existing page for `same_file`, replace it for `new_version`, keep both for `title_clash`. The usual review
   flags (personal data, broken links, verifier) still apply and are listed beside it.
3. The notification is where pending knowledge already shows: the Review tab of the wiki (personal, org), its
   badge, the dashboard "needs attention" count, and the Knowledge > Suggestions inbox, which lists it as "Held
   upload - possible duplicate" with the proposal and a Decide link instead of a one-click Approve.
4. The upload banner (and the first-run queue's notification) says the document was not added and is held for the
   user's decision. It never says "added".
5. The review page offers three choices on a held upload, the recommended one highlighted; nothing changes until one
   is chosen:
   - Keep existing: the held page is discarded (the existing reject action).
   - Replace existing: the held page, with any edits, is written over the existing page (the existing approve
     action; the page keeps its slug and takes the new title).
   - Keep both: the existing page is untouched and the held page is written under its own title with
     `(uploaded <date>)` appended (a counter is added if that slug is taken), then approved.
6. Everything else is as before: a page that matches nothing publishes, flagged pages queue, and the personal,
   team and org scopes all use the same path. This covers uploads, confirmed large PDFs, connector imports and the
   first-run upload queue.

## Known limits

- The model still runs before the comparison, so a repeat upload costs a full ingest. Only the result is held.
- The same-file match relies on the source hash in a page's provenance. Pages ingested before provenance was
  recorded, and pages written by hand, can only be matched by slug.
- Two uploads of one file while the first is itself still held do not see each other: both wait, and approving
  both writes the second over the first.
- A near-duplicate with a different slug and different source (the same facts in another file) is not a conflict
  here; the review gate's existing "duplicate" title check still applies to it.
- Team uploads are held in the team's review list on the team page. That list has no decision buttons yet (this
  was already true of every flagged team page), so a team owner decides through the review actions directly.

## Acceptance

- Uploading a file, then the same bytes again (also under another file name, or with the model returning another
  title), leaves one page file unchanged and one pending review flagged `same_file` whose text recommends keeping
  the existing page; the redirect carries `saved=held`.
- A different file with the same heading leaves the existing page unchanged and queues a `title_clash` review; a
  changed file of the same name queues `new_version`.
- Keep existing discards, Replace existing overwrites and Keep both adds a second page, as above.
- The held upload shows on the review page and in the Suggestions inbox, the latter without an Approve button.
- A new document still publishes; an org upload is held for the admin; a first-run queued upload is held and the
  user is told.
- Tests: `tests/test_wiki_duplicate_uploads.py` (model-free).
