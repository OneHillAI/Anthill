# Spec: fresh wikis couldn't accept their first upload

Status: implemented. Lane: `pillar:feature`.

## Problem

Founder report on the installed v0.12.10 build: "document upload on the knowledge doesn't really
work - it doesn't upload it, doesn't do anything, nor show the uploaded docs after." Reproduced
headlessly and confirmed live against a real local model; two independent, unrelated root causes.

### Bug A: every upload was silently stranded in the review queue, never published

`/wiki/upload` -> `_ingest_and_propose` -> `ingest()` summarises the document into a page. The
summariser routinely closes with a "## Related" section of `[[wiki-links]]` it infers from the
document's own content. `propose_wiki_write` (the single gate for every wiki write) runs
`outline_change` on that page before writing it: a mechanical check flags `broken_links` for any
`[[link]]` whose target page doesn't exist yet, and a flagged write becomes a pending `WikiReview`
instead of a page - nothing appears on `/wiki`, and the founder's "?saved=added" vs "?saved=queued"
distinction was never surfaced clearly enough to tell the two apart.

On a fresh or sparse wiki, the model's own generated cross-references are *always* dangling (there's
nothing yet for them to point at), so this mechanical gate flagged the model's own output on every
single upload - the very first document a user ever adds can never land. #428 already skipped the
separate model-judgement review pass for personal wikis to stop exactly this kind of self-inflicted
stranding, but the mechanical broken-links check - fed by the same model that wrote the links - was
left on and hits the identical failure mode.

### Bug B: the upload picker blocked formats the backend already supports

`wiki.html`'s file input hardcoded `accept=".md,.txt,.pdf,.png,.jpg,.jpeg,.gif,.webp"`, while
`SUPPORTED_UPLOAD_EXT` in `app.py` also includes `.docx`, `.pptx`, `.xlsx`, `.html`, `.htm` - the
two lists had drifted apart, so Word/PowerPoint/Excel/HTML documents were greyed out in the file
picker even though the server would have accepted and correctly ingested them.

## Fix

**Bug A** - `anthill/wiki/ingest.py`, a new `_neutralize_dangling_links(page_md, ws, self_title)`,
called inside `ingest()` right after the page's title is known and before `tag_page` appends its
own trailing provenance comment (that comment isn't wiki-link content, but the "is this whole
section now dangling" check below matches to the end of the page, so it has to run first):

- Any `[[link]]` anywhere in the page whose target has no existing page yet, and isn't a
  self-reference, loses its brackets but keeps its text - a reader still sees "Records Retention",
  just not as a link. A link to a page that genuinely exists, or to the page's own title, is left
  untouched.
- A "## Related" section (in practice always nothing but links; `normalize_wiki_page` has already
  joined its lines into one) is judged as "nothing but links" by removing every `[[link]]` plus
  ordinary list punctuation and checking whether anything of substance remains - not by assuming a
  particular bullet/line shape, since the model's own formatting and the line-joining upstream both
  vary. If the whole section turns out to be nothing but dangling links, the entire heading and body
  are dropped, rather than shipping a "## Related" heading over a list of now-bare, delinked words.
  A section with real prose, or at least one link whose target already exists, is left alone (its
  dangling entries still get individually delinked).

This only runs on ingest's AI-generated links. A person typing a genuinely broken `[[link]]` during
a manual page edit goes through `outline_change` exactly as before and is still caught - nothing
about the mechanical check itself changed, only what ever reaches it from a fresh ingest.

**Bug B** - `_wiki_ctx` (`app.py`) now passes `"upload_accept": ",".join(sorted(SUPPORTED_UPLOAD_EXT))`
into the template context, and `wiki.html`'s file input uses `accept="{{ upload_accept }}"` instead
of a second, hand-maintained copy of the list - the two can no longer drift apart.

## Verification

`tests/test_wiki_dangling_links_fix.py` (new, 12 tests): the delinking behaviour in isolation
(dangling loses brackets/keeps text, a real link is untouched, a self-reference is untouched); the
"## Related" section handling in all three shapes (all-dangling -> whole section dropped; one real
link -> section kept, only the dangling entries delinked; non-link prose -> section kept untouched);
`outline_change` still flags a genuinely broken link on a manual edit; the actual reported failure
mode reproduced end-to-end (`ingest` then `outline_change` against a fresh wiki - flags go from
`["broken_links"]` to `[]`); and the file-picker's `accept` list matches `SUPPORTED_UPLOAD_EXT`
exactly.

`tests/test_document_upload.py`'s existing `test_flagged_upload_survives_the_request_boundary_and_
can_be_approved` (#827) relied on a dangling `[[link]]` to force a flag through the real
`/wiki/upload` route - after this fix that link is neutralised and no longer flags anything, so the
test's premise broke. Updated to force the same kind of mechanical, model-independent flag via a
near-duplicate title instead (still exercising the exact thing #827 cares about: a flagged review
surviving the request boundary and later being approvable) - confirmed this substitution changes
nothing about what that test proves.

Full suite: 2807 passed, 8 skipped. `ruff check`/`ruff format --check` and the em/en-dash slop gate
both clean.

Also verified live against a real local model (qwen3.5:9b) through the actual browser UI, not
mocked: on a genuinely empty wiki, the first-ever upload published immediately
(`?saved=added`, not `?saved=queued`). A second upload, over a wiki that now had one real page,
produced a real "## Related" section from the model with three links - one to the page that already
existed (kept as a live `[[link]]`), two dangling (correctly delinked to plain text, section kept
since not all of it was dangling) - confirming the exact mixed-section behaviour this fix implements,
end to end, with no mocking anywhere in the chain.
