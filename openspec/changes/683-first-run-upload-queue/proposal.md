# Phase 5 of #683: first-run upload queue while the local model downloads

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` (requirement 4) found that a user who uploads a
document while their local model is still downloading on first run gets a failure instead of a
queued document - and that the wait itself is a blank screen with no guidance. This is the fifth
of seven phases splitting that spec into independently shippable PRs. It is INDEPENDENT of phase 4
(`683-ingestion-formats`, the other half of requirement 4) - both branch from `main` directly, not
stacked on each other, per the phase plan - and independent of phases 2-3 (a sibling agent's
concurrent work).

Verified against the real code before building, not assumed from the spec's own description:
- `OrgSettings.local_model_pulling` (`anthill/web/db.py`) is a string column - the tag currently
  downloading, or `""` when idle/done - already read by `GET /models/pull-status` (the Models
  page's own poller) and set/cleared by `_start_model_pull`/`_set_model_pulling` in
  `anthill/web/app.py`. This is exactly the flag the spec means by "the local model is still
  downloading".
- `wiki_upload()` had no dedicated handling for this state: if the configured model is not yet
  installed, `_backend_from_cfg(cfg)` builds a backend that will fail when `ingest_file()` tries to
  call it, which is caught by the route's generic `except Exception` and redirected with
  `?error=ingest` - the exact "Couldn't read that document - is the model running? Try again."
  copy the grounding predicted.
- The dashboard (`anthill/web/templates/dashboard.html`) already has a first-run
  `#local-model-banner` that polls `GET /local-model/status` and shows "Preparing your local
  AI... You can keep exploring; chat will be ready when this finishes." - confirmed this is
  exactly the "blank wait screen" the spec describes: it names no concrete next step.
- The existing `?saved=queued` query param already means "flagged for human review, waiting in the
  review queue" (`wiki.html`) - a different concept from "the model isn't even ready yet" - so the
  new state needed its own distinct param, not a reuse.

## What

- `anthill/web/db.py`: new `QueuedUpload` table (`org_id`, `user_id`, `target_scope`, `team_id`,
  `filename`, `stored_path`, `status` [`queued`/`done`/`error`], `error`, `created_at`,
  `processed_at`). Additive - `Base.metadata.create_all` picks it up on existing installs with no
  migration-registry entry, the same pattern already used for `ProposedSkill`/`TaskRun` (confirmed
  neither appears in `anthill/web/migrate.py`).
- `anthill/web/app.py::wiki_upload()`: an early check - if `cfg.local_model_pulling` is truthy,
  the uploaded bytes are saved durably (`_store_queued_upload`, per-org under `ANTHILL_FILES_DIR`,
  NOT the request's ephemeral tempdir, which is long gone before a multi-GB download finishes), a
  `QueuedUpload` row is inserted, and the response redirects with `?saved=queued_for_model` -
  distinct from the existing `?saved=queued` (human review queue). `GET /local-model/status` also
  now reports the caller's queued-upload count, for the dashboard banner.
- `anthill/web/scheduler.py`: new `_process_queued_uploads_tick` (registered in the existing
  slow-tick group alongside `_backfill_review_outlines`) that, for every org whose
  `local_model_pulling` is currently falsy, ingests each of that org's `status="queued"` rows
  through the same `wiki.ingest.ingest` + `propose_wiki_write` pipeline `wiki_upload()` itself
  uses (lazily imported from `.app`, the same late-import pattern `_event_tick` already uses to
  avoid a load-time circular import), marks the row `done`/`error`, deletes the durable file
  either way, and notifies the uploader via the existing `notify()` helper.
- `anthill/web/templates/dashboard.html`: the existing (previously silent) "preparing your local
  AI" banner now also links to Wiki/Skills setup and reports queued uploads by count, so first run
  is a guided step instead of a blank wait.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 4 (effortless, consistent
ingestion) - the first-run model-download guided-step and queued-upload bullet specifically. The
ingestion-formats and connector-pipeline-unification bullets ship as a separate, independent phase
of this program (`683-ingestion-formats`).
