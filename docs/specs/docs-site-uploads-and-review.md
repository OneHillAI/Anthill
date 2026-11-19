# Spec: the published guide describes how uploads are read, held and reviewed

Status: implemented. Lane: `pillar:knowledge`. Issue: #280. Follows #239 (PR #244). Related: #191, #197, #203, #218, #224, #225, #258, #271, #279.

## Problem

Between 2026-11-15 and 2026-11-19 the way a document is read, held, reviewed and used in an answer changed in a dozen
merged PRs. The pages published at docs.anthill.run described almost none of it, and `docs/setup.md` named a screen that
had been folded away:

1. Held uploads, flagged pages and the AI's proposals became one "Needs review" tab under Knowledge (#279). The pages
   still said "Wiki > Review", "wiki review queue" and "wiki review".
2. A held upload (same file, same title, or a possible newer version) and the choices on its card were not described
   (#191, #271; `wiki_review.html`).
3. The upload record listed six statuses; the app has eight (`upload_status.py`), a retry with a longer limit, and PDF
   limits of 200 pages, 25 MB and 10 minutes (`PDF_HARD_MAX_PAGES`, `PDF_HARD_MAX_SOURCE_BYTES`,
   `PDF_HARD_PROCESSING_SECONDS` in `anthill/multimodal/reader.py`).
4. The Files tab's "text read from it" link and the page footers ("AI summary written by ...", "The note as uploaded")
   were not described (#225, #218, #201).
5. Answers can now be given passages of the original document text, with "Documents in context" and "Pages in
   context" lines (#258, #224), which the Chat section did not say.

## Requirements

1. The five published pages that named the old review screen SHALL say "Needs review" (under Knowledge) instead, with
   each sentence's meaning unchanged: `docs/setup.md`, `docs/USING_ANTHILL.md`, `docs/using-chat-and-knowledge.md`,
   `docs/using-models-and-compute.md`, `docs/how-it-works.md`.
2. `docs/using-chat-and-knowledge.md` SHALL describe a held upload: when it happens, that nothing is written until the
   person chooses, the choices for a duplicate or title clash (Keep existing, Replace existing, Keep both, one
   recommended), and for a possible newer version (side-by-side with changed words marked; replace, keep both, or keep
   the old and discard the new).
3. It SHALL list all eight statuses, the retry with a longer limit, and the PDF limits and the larger-PDF confirmation.
4. It SHALL describe the "text read from it" link and the two page footers, and say that nothing is kept for a note or a
   scan.
5. Its Chat section SHALL say that answers can be given passages of the original text, and what "Documents in context"
   and "Pages in context" mean (what the model was given, not what the answer relied on).
6. Every figure and label SHALL be one the code shows. The item records, `anthill knowledge inventory` and the
   `ANTHILL_DOC_CONTEXT` switch are for operators and contributors and SHALL NOT be added.
7. Headings, page order, links between pages and the publish whitelist SHALL NOT change.

## Known limits

- Models, setup, Settings, dashboard and chat-page changes, backup details and privacy changes are separate documentation
  issues.
- The pages say "Needs review, under Knowledge". If the tab is renamed again the pages need the same edit.

## Acceptance

- Each statement matches the template or constant named above.
- A search of the five pages for "Wiki &rarr; Review", "wiki review queue" and "wiki review" finds nothing.
- The doc tests pass and `node docs-site/scripts/collect-docs.js` copies all eight pages.
- After merge, the live pages show the new text.
