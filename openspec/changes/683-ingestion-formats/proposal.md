# Phase 4 of #683: Office/HTML ingestion + connector-import pipeline unification

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` (requirement 4) found that Office documents
cannot be ingested at all, and that connector imports (Drive/Notion) are filed as raw text instead
of going through the same convert-then-summarise pipeline as an uploaded file - so a Drive import
and an uploaded file of the same content produce differently-shaped wiki pages. This is the fourth
of seven phases splitting that spec into independently shippable PRs; it is independent of phases
2-3 (a sibling agent's concurrent work) and of phase 5 (the first-run model-download queue, the
other half of the same requirement).

Verified against the real code before building, not assumed from the spec's own description:
- `anthill/multimodal/reader.py::read_file()` dispatched only `.pdf`, image extensions, and a UTF-8
  plain-text fallback. `_read_pdf()` was the only MarkItDown converter wired up
  (`MarkItDown(enable_builtins=False, enable_plugins=False)` plus a manually-registered
  `PdfConverter()`), and `pyproject.toml` only requested the `markitdown[pdf]` extra.
- `anthill/web/app.py::SUPPORTED_UPLOAD_EXT` is a second, genuinely independent extension gate on
  uploads - fixing the reader alone would not have unblocked uploads.
- `anthill/web/app.py::wiki_import_connector()` built the page body directly
  (`text if text.lstrip().startswith("#") else f"# {label}\n\n{text}\n"`) and called
  `propose_wiki_write` directly, never touching `wiki.ingest.ingest()` - confirming the
  raw-text-filing gap the spec describes.
- `wiki.ingest._preflight_pdf_work()` already only acts when `fc.mime_type == "application/pdf"`,
  so it is a correct no-op for the new non-PDF `FileContent` values - no change needed there.

## What

- `pyproject.toml`: `markitdown[pdf]` -> `markitdown[pdf,docx,pptx,xlsx]` (HTML needs no extra -
  MarkItDown's `HtmlConverter` only needs `beautifulsoup4`, already a base dependency of
  `markitdown` itself). Verified against markitdown 0.1.7's live PyPI metadata on 2026-08-02;
  re-check before merge in case a newer release renames or removes these extras.
- `anthill/multimodal/reader.py`: new `_read_office()` mirrors `_read_pdf()`'s pattern (a
  locked-down `MarkItDown` instance, exactly one converter registered, fed a local stream plus
  explicit `StreamInfo`) for `.docx`/`.pptx`/`.xlsx`/`.html`/`.htm`, deliberately WITHOUT the PDF
  path's safety-limit and image-extraction machinery: no page/image preflight is computed, and no
  embedded images are extracted from Office documents. Office-format image extraction is future
  work, not part of this phase.
- `anthill/web/app.py`: `SUPPORTED_UPLOAD_EXT` gains the four new extensions. New shared
  `_ingest_and_propose()` helper (ingest-in-threadpool + slugify + `propose_wiki_write`) used by
  both `wiki_upload()` and `wiki_import_connector()`, so the two routes cannot drift back into
  producing differently-shaped pages. Connector imports are now written to a temp `.md` file (same
  `tempfile.mkdtemp()` pattern the upload route already used) and routed through that shared helper
  instead of being filed as raw text.
- `anthill/web/templates/wiki.html` and the upload route's docstring: updated "Supported: ..."
  copy to list the new formats.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 4 (effortless, consistent
ingestion) - the ingestion-formats and connector-pipeline-unification bullets specifically. The
first-run model-download guided step and queued-upload bullet ships as a separate, independent
phase of this program (`683-first-run-upload-queue`).
