# Spec: what the wiki says about an upload is what happened to it

Status: implemented. Lane: `pillar:knowledge`. Knowledge roadmap: the wording part of item K1 (faithful ingest).
Builds on `docs/specs/wiki-upload-feedback-and-status.md` and `docs/specs/181-extract-only-short-notes.md`.

## Problem

Three things the wiki said after an upload were not true or not clear:

- The success banner read `"<title>" added to this wiki - indexed by your machine.` The label came from the
  account's settings (`_compute_where_label`), not from what ran. The upload's own backend is built separately
  and ignores that routing, nothing is "indexed by" a model (page vectors are made later, in the background), and
  a short note filed as written (#181) never touched a model at all.
- The page itself said `Written by <model> on <date> from <hashed file name>.` Nothing told the reader that the
  page is the model's summary, not their text, and the source was the internal name
  (`report-1a2b3c4d5e6f.pdf`) as plain text, with no link to the kept original.
- The "unsupported file type" message listed `.md, .txt, .pdf` and images, while the page said `.docx`, `.pptx`,
  `.xlsx` and `.html` are fine, and both lists were typed by hand next to the set the route checks.

## Requirements

1. The banner for an added or flagged upload says what read the document, from the person's own latest upload
   record for that page (the model the page is tagged with): "Read by <model>, which wrote the page from your
   document.", or "Filed exactly as written: no AI rewrote it." for a note filed without a model. It adds that the
   original is kept under Files, with a link to that tab. No "indexed by", and no label from the account's
   settings. When there is no such record, or it names no model, the banner says nothing about how it was read.
2. The record is looked up by org, user, scope, team and slug, so a link made by hand, or another person's
   upload, gets no such sentence.
3. A page written by a model shows: "AI summary written by <model> on <date> from <original name>. It is not the
   text of your document; the original is kept under Files." A note filed as written keeps "The note as uploaded
   on <date> from <original name>." The original name is the one uploaded (the `-<12 hex>` the kept copy carries is
   removed). It is a link to the kept original when that file exists, and plain text when it does not.
4. The "unsupported file type" message and the standing "Supported:" line under "Add a document" are built by one
   function from `SUPPORTED_UPLOAD_EXT`, the set the route checks.
5. The upload redirects no longer carry a `via` parameter.

## Known limits

- "AI summary" is the pipeline's output for every page a model writes: one page of a title, a one-line summary and
  facts. A page made from an image or a scan is a description of it, and is still called a summary.
- The banner reads the upload record, so it depends on that record being written (it is, for every web upload).
  A page filed by the inbox, a webhook or the command line has no record and gets no "how it was read" sentence.
- Nothing here says what the summary left out, or shows progress while the model reads. Those are separate items.

## Acceptance

- After a model-read upload the banner contains "Read by <model>, which wrote the page from your document." and
  "The original is kept under Files", and not "indexed by".
- After a short note the banner says it was filed exactly as written and not "Read by".
- A hand-made `?saved=added&slug=...` link shows no "how it was read" sentence.
- The unsupported message and the standing line are the same sentence, and it names `.docx`, `.pptx`, `.xlsx` and
  `.html`.
- The page view says "AI summary written by <model>" with the uploaded name as a link whose target opens the
  kept original byte for byte (`tests/test_honest_upload_messages.py`).
