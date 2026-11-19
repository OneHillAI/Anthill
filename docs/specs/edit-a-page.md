# Spec: a person can correct a wiki page

Status: implemented. Lane: `pillar:knowledge`. Issue: #259, step 2 of the knowledge base update, epic #270. Builds on
`docs/specs/item-records-and-inventory.md` (the records). Architecture: `anthill-internal/knowledge-pillar/architecture.md`.

## Problem

A page made from an upload is the model's summary, and a summary can be wrong: a handbook says the flour comes from the
Brandt mill and the page says Flurf. The page view is read-only and no route edits a page, so a person who sees the mistake
cannot fix it.

## Requirements

1. **Edit from the page.** `GET /wiki/page/<slug>/edit` shows the page's own text, in Markdown, with no front matter and no
   provenance comment. `POST /wiki/page/<slug>/edit` saves it. The page view shows "Edit this page" to whoever may edit.
2. **Who may edit** is the same rule as removing (`_wiki_can_edit`): anyone in their own wiki, a team owner in a team's wiki,
   an admin (never a Solo account) in the organisation's. Someone who can read the page but not edit it gets 403; a scope or
   page that is not theirs is 404. An edit never creates a page, and the file must be inside the wiki folder.
3. **The edit is a direct write**, in every wiki. The people who may edit a shared wiki are the people who approve changes to
   it, so an edit by one of them does not go through the review queue.
4. **The record.** The page keeps its id; its version goes up, the hash it replaced is kept as the parent hash, its origin
   becomes `person-edited` and its review state `checked`. A page with no record gets one with the same values. The page keeps
   its provenance comment, so it still knows its source, and its line reads "AI summary written by M on D from S, then edited
   by a person." A provenance comment typed into the box is removed.
5. **A model upgrade never regenerates a page a person edited**: `stale_pages` leaves it out and `renovate` reports it as
   skipped ("edited by a person").
6. **What is refused**, with the person's text given back and nothing written: an empty page, and a page over
   `MAX_PAGE_EDIT_CHARS` (100,000 characters, several times the largest page an upload writes). Text equal to the page's
   current text writes nothing and does not mark the page edited.
7. **The rest follows from the write.** The slug does not change when the first heading does. The index, the log (`edit`), the
   knowledge registry and the page's embedding (queued in the background) are brought in line. The page is read from disk for
   every answer, so the next answer is given the corrected text, and a cached answer made from the old page is dropped by the
   existing freshness check. No model is called.
8. **A person who does not edit is unaffected.** Nothing is asked, and no page changes.

## Known limits

- **An edit overwrites the page and the earlier text is not kept.** The record keeps the version number and the hash of the
  text it replaced, not the text. The edit page says so. Version history is a separate piece of work.
- **Last save wins.** Two people saving the same page at the same time are not told; the second save replaces the first.
- Another writer can still replace a page a person edited through an approved "replace" in the review queue, which is a person's
  own decision. An upload of the same file is held for a decision and never written over the page. A page an agent proposes goes
  to the review queue (#295).
- The edit form is one text box with no preview. A `[[link]]` to a missing page is plain text, as on the page view.
- Delete is `docs/specs/remove-an-item.md`. Rename, merge and the replace flow ("replace the old one, or keep both?") are not
  built here.

## Acceptance

All in `tests/test_edit_a_page.py`:

- The form shows the page's text only; saving replaces it, the page view shows it, the log and index follow; the correction is
  what the next answer is given (the reproduced "Flurf" to "Brandt mill" case).
- The record: same id, version 2, parent hash, origin `person-edited`, review `checked`, links and source kept; a page with no
  record is edited too; a typed provenance comment is dropped.
- The page line says "then edited by a person" after an edit and not before.
- Empty and over-long text are refused with the text given back and the file untouched; unchanged text writes nothing; an unknown
  or path-like slug is 404 and no page is created.
- The permission matrix: own wiki, team owner, team member, an admin outside the team, org member, org admin, a Solo account, for
  the form, the save and the link.
- `stale_pages` and `renovate` leave an edited page alone; no model is called.

## Not verified here

Nothing has been run on a real install or in a browser, only through the test client, so the edit page's look has not been seen.
