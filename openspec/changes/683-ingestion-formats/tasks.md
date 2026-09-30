# Tasks

- [x] Verify `read_file()`'s current suffix dispatch, `_read_pdf()`'s MarkItDown wiring, the
      `markitdown[pdf]` pin, `SUPPORTED_UPLOAD_EXT`, `wiki_import_connector()`'s raw-text page
      body, and `wiki.ingest._preflight_pdf_work()`'s PDF-only guard against the real code before
      building (not assumed from the spec's own description).
- [x] `pyproject.toml`: extend the markitdown extra to `[pdf,docx,pptx,xlsx]`; checked the live
      PyPI metadata for markitdown 0.1.7 to confirm extras naming and that HTML needs none.
- [x] `anthill/multimodal/reader.py`: add `_read_office()` + suffix dispatch for
      `.docx`/`.pptx`/`.xlsx`/`.html`/`.htm`, explicitly without PDF's safety-limit/image-extraction
      machinery.
- [x] `anthill/web/app.py`: extend `SUPPORTED_UPLOAD_EXT`; add `_ingest_and_propose()` shared
      helper; refactor `wiki_upload()` and `wiki_import_connector()` to both use it instead of
      duplicating (or, for the connector route, skipping) the convert-then-summarise pipeline.
- [x] `anthill/web/templates/wiki.html` + route/CLI docstrings: update "Supported: ..." copy.
- [x] Confirmed `wiki.ingest._preflight_pdf_work()` is already a correct no-op for non-PDF
      `FileContent` (only acts when `mime_type == "application/pdf"`) - no change needed.
- [x] Tests: new `tests/test_office_ingest.py` - real `.docx`/`.pptx`/`.xlsx`/`.html` fixtures
      built with python-docx/python-pptx/openpyxl (not garbage bytes with a faked extension),
      exercised both at the reader level (`read_file`) and through the full `ingest()` pipeline.
      Updated `tests/test_wiki_import_connector.py`'s review-gate test for the new
      summarised-page behaviour (it previously asserted the old raw-text passthrough) and added a
      backend-failure-mapping test.
- [x] Full local validation: `ruff check anthill tests`, `ruff format --check anthill tests`,
      `mypy anthill`, full `pytest -q` - 2136 passed, 7 skipped (pre-existing, unrelated
      browser-test skips), 0 failed.
- [x] Live-verified in a real running instance (fresh org, solo/local topology, real Ollama
      `qwen2.5:3b`): uploaded a real `.docx` and a real `.html` file through the actual
      `/wiki/upload` HTTP route. Both were converted by MarkItDown and summarised by the real
      local model into structured pages (extracted facts, not raw/garbled bytes), correctly
      flagged into the review queue (visible in the dashboard and the Wiki review UI), and after
      approving one, it landed on disk as a proper OKGF-frontmatter markdown page with the
      original `.docx` preserved under `raw/`. Connector-import unification was NOT live-verified
      (the fresh test org had no document-source connector configured) - relied on the updated
      `tests/test_wiki_import_connector.py` for that path instead, since it is pure route-level
      glue reusing the already-live-verified `_ingest_and_propose()` helper.
- [x] Mark requirement 4's ingestion-format half shipped in the spec.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via
      the app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec`
      sections. Monitor check-runs to completion. Do NOT merge.
