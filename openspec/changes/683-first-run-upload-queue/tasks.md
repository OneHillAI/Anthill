# Tasks

- [x] Verify `OrgSettings.local_model_pulling`'s real usages (`GET /models/pull-status`,
      `_start_model_pull`/`_set_model_pulling`), `wiki_upload()`'s actual failure mode with no
      model ready, the dashboard's existing (silent) first-run banner, and the existing
      `?saved=queued` meaning against the real code before building.
- [x] Confirmed the new-table-needs-no-migration-entry pattern by checking `ProposedSkill`/
      `TaskRun` are absent from `anthill/web/migrate.py` and that `create_tables()` calls
      `Base.metadata.create_all(engine)` unconditionally.
- [x] `anthill/web/db.py`: add `QueuedUpload` table.
- [x] `anthill/web/app.py`: add `_store_queued_upload()` helper; add `wiki_upload()`'s early
      `local_model_pulling` check + `?saved=queued_for_model` redirect; extend
      `GET /local-model/status` to report the caller's queued-upload count.
- [x] `anthill/web/scheduler.py`: add `_process_queued_uploads_tick` + `_process_one_queued_upload`
      (lazily importing `propose_wiki_write`/`_backend_from_cfg`/`_wiki_page_url` from `.app`, the
      existing late-import pattern used to avoid a load-time circular import); register the tick
      in the slow-tick group.
- [x] `anthill/web/templates/wiki.html`: add the `?saved=queued_for_model` alert copy, distinct
      from the existing `?saved=queued`.
- [x] `anthill/web/templates/dashboard.html`: extend the existing first-run banner with Wiki/Skills
      links and a queued-upload count, driven by the extended `/local-model/status` poll.
- [x] Tests: new `tests/test_first_run_upload_queue.py` - uploading while the model is pulling
      queues instead of failing (and does not touch the backend at all); the scheduler tick
      processes a queued row once `local_model_pulling` clears (and does NOT touch a still-pulling
      org's queued row); the row and stored file are cleaned up after processing (done and error
      paths); the uploader is notified either way; `/local-model/status` reports the queued count.
- [x] Full local validation: `ruff check anthill tests`, `ruff format --check anthill tests`,
      `mypy anthill`, full `pytest -q` - 2132 passed, 7 skipped (pre-existing/unrelated), 0 failed.
- [x] Live-verified in a real running instance: set `local_model_pulling` on a fresh org's
      settings row directly, uploaded a real file through `/wiki/upload`, confirmed it queued
      (`?saved=queued_for_model`, a `QueuedUpload` row, the durable file on disk, no wiki page
      yet) instead of failing; cleared the flag and called `_process_queued_uploads_tick` directly
      against the running instance's own database, confirmed the row flipped to `done`, the file
      was deleted, a notification was created, and the wiki page appeared for real (summarised by
      the real local Ollama model, not mocked).
- [x] Mark requirement 4's first-run-queue half shipped in the spec.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via
      the app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec`
      sections, noting it is INDEPENDENT of phase 4 (no stacking). Monitor check-runs to
      completion. Do NOT merge.
