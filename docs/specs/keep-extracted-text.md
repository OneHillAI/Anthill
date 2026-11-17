# Spec: keep the text Anthill extracts from a document

Status: implemented. Lane: `pillar:knowledge`. Knowledge roadmap: the first step of item K1 (the source is kept
verbatim as a linked layer) and of item K18 (raw sources searchable). Related:
`docs/specs/honest-upload-messages.md`, `docs/specs/wiki-upload-feedback-and-status.md`.

## Problem

An upload is kept twice: the original file under `raw/`, and one wiki page, which is the model's summary. The text
the reader takes out of a `.pdf`, `.docx`, `.pptx`, `.xlsx` or `.html` to give the model is used once and thrown
away. So:

- a person cannot see what the AI was given, only what it wrote, and cannot tell a reading problem (a scrambled
  table, a missing page) from a summary problem;
- nothing can search the document itself. Retrieval sees only the summary page, of which only the first 2,000
  characters are embedded, and the original binary is never read for an answer.

## Requirements

1. When ingest has read a source and kept its original, it also keeps the extracted text as
   `text/<original's name>.txt` in the same workspace (`Workspace.text`, made on first use), written as UTF-8
   through a temporary file and a rename. Keeping it again replaces it.
2. Nothing is kept for a `.md` or `.txt` (the original is the text), and nothing when the reader found no text: the
   one-line stand-ins it returns for a scan or an image ("[PDF: a.pdf - no extractable text. ...]",
   "[Image: a.png]") are not text from the document. A document whose real text only begins with a bracketed line
   is kept.
3. A text longer than `EXTRACTED_TEXT_MAX_CHARS` (5,000,000) is cut there and ends with a line saying so. The cut
   applies to the kept copy only: the text the model is given is the reader's text, unchanged. (5,000,000
   characters is about a million tokens, far above what a 25 MB upload yields in practice; it only stops a
   pathological file from filling the disk.)
4. Keeping it is best effort: a failure to write is logged and never fails or slows the upload.
5. The Files tab shows "text read from it" beside each original that has one, and says what it is. The wiki page's
   line for a model-written page adds "you can open the text Anthill read from it" when it exists.
6. `GET /wiki/text/<original's name>` serves it as `text/plain; charset=utf-8`, inline, with
   `X-Content-Type-Options: nosniff`. It answers for a name that is a bare file name, is not a symlink, has a kept
   original and a kept text; anything else is 404. The scope and membership rules are those of
   `GET /wiki/raw/<name>`, and each view is audited as `wiki.text_view`.

## Known limits

- It is the text as the reader produced it: PDF pages are joined, tables and slides come out as Markdown, embedded
  images of a Word, PowerPoint or Excel file are not in it. It is not a faithful copy of the layout.
- Nothing searches it yet. Making it part of what an answer can use (chunks with their positions, vectors from the
  persistent index, a retrieval path) is the next step and is not done here.
- It is stored in plain text like the originals and the pages. Backups include it (they archive whole wiki
  folders). The OKGF export does not, as it does not include the originals.
- Documents uploaded before this change have no text kept. Re-uploading one keeps it; a re-read of old originals is
  not done here.
- Nothing deletes it, because nothing deletes a wiki page or a kept original today. The only place ingest removes an
  original is a PDF it could not read, and the text is kept only after a successful read, so that removal cannot
  leave a text behind (`test_a_pdf_that_cannot_be_read_leaves_no_original_and_no_text`). Deleting a document and
  everything derived from it is Knowledge roadmap item K3. Whoever builds it must remove
  `text/<original's name>.txt` with the original; `test_every_kept_text_has_its_original_through_ingest` shows how
  to check that no text is left without its original.
- Scans and images have no text kept, because the vision model's description is derived, not extracted.

## Acceptance

- Ingesting an HTML or PDF file leaves `text/<original's name>.txt` with the extracted text and no markup.
- Ingesting a note, a scan, an image or an empty document leaves nothing.
- A text over the limit is cut with the marker line; keeping it twice leaves one file and no temporary file.
- A write failure returns nothing and does not raise.
- The Files tab and the page show the link for a PDF upload and not for a note; the link opens the text as plain
  text with `nosniff`; a missing, traversing or another person's name is 404
  (`tests/test_extracted_text_kept.py`).
