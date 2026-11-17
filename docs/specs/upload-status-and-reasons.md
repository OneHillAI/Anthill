# Spec: an upload leaves a record, and a failure says what to do

Status: implemented. Lane: `pillar:knowledge`. Related: #148 (timed-out uploads), #149 (duplicate uploads), #181 (short notes filed as written).

## Problem

A person uploaded a PDF to the wiki and could not tell afterwards whether the AI had read it, what became of it or
what the result meant: "a document gets uploaded but not shown as done". Three causes, found by reproducing on a
fresh Solo install with a fake model:

- The only feedback was a one-time banner built from the query string, gone on the next page load. Nothing
  recorded that an upload had happened.
- A scanned (image-only) PDF with a text-only model raises `BackendError('PDF images require a local Ollama vision
  model.')`. The upload handlers turned every exception into `?error=ingest`, whose banner said "Couldn't reach your
  AI", which was wrong and gave no way forward.
- An upload made while the local model was still downloading was queued and never reported again.

The founder's rule (2026-10-01): a technical reason with no action is not help. Each message says what happened and
what the person can do next, without jargon.

## Requirements

1. Each upload through `/wiki/upload`, `/wiki/upload/confirm`, `/wiki/upload/retry/{id}` and `/wiki/import-connector`
   writes an `UploadRecord` (owner, scope, team, file name, status, reason code, page slug and title, the model that
   wrote the page, start and finish times) when it starts and updates it when it ends. The scheduler updates the
   record of a queued upload when it processes it, so "waiting for the model" becomes Added or Couldn't read by
   itself. A retry updates the record of the timed-out upload instead of adding a second line, and choosing a
   timed-out file again and succeeding removes the old Timed out line.
2. The wiki page lists the person's own last ten uploads for that scope under **Add a document**, newest first, each
   with the file name, the time, a status label, one sentence saying what it means and what to do, the page link when
   added, and `read by <model>` from the page's provenance tag (`filed as written` when the tag says `verbatim`).
   Statuses: Reading, Added, Waiting for your review, Held for your decision, Waiting for the model to finish
   downloading, Timed out (pointing at the existing retry button), Couldn't read, and Interrupted - try again for a
   record still Reading after the longest allowed processing time plus the two minutes the page's progress bar waits
   (12 minutes). Only the owner sees a record, an org admin included.
3. A failed read is mapped from the real exception, and each message names the next step:
   - a scan or picture with no image-reading model: "That PDF is made of page images (a scan) and your current AI
     reads text only. Add an image-reading model in Settings > Model, or upload a text version." with a Settings
     link (variants for a picture, for a model running on another machine, and for a PDF with no readable pages);
   - "Couldn't reach your AI" only for a connection failure: a network error in the exception chain, or a backend
     whose `health()` reports a problem. Anything else is "Anthill couldn't read that document" with advice to try
     again or upload another format;
   - a model call that outlasts its limit on a connector import: "took too long ... try again in a few minutes";
   - a PDF that needs confirmation: tick the "Confirm larger PDF processing" box, choose the file again, click Upload;
   - a PDF over the hard limits: the limits (200 pages, 25 MB, 10 minutes of reading) and to split the file;
   - a transient PDF failure: the file is fine, wait a minute and try again.
4. The upload status bar ends by pointing at Recent uploads.
5. What is stored in the wiki and how documents are summarised do not change. Records are best-effort: a failure to
   write one never stops an upload.

## Known limits

- The image-reading cases are recognised from the reader's own messages in `wiki/ingest.py` (`BackendError` has no
  subclasses). `tests/test_upload_status.py` raises each real message to keep the match in step; a reworded message
  falls back to the generic "couldn't read" text, never to "couldn't reach your AI".
- The list shows only the person's own uploads for the scope they are viewing. An org admin does not see members'
  uploads.
- Uploads rejected before reading starts (unsupported type, empty file, over 25 MB, no permission) keep their banner
  and get no record. Nothing is filed for them.
- A record whose process was killed stays Reading in the database and is shown as Interrupted; it is not cleaned up.
- Whether a real image-reading model reads a real scan is not tested here (no model is run).

## Acceptance

- A text PDF upload creates a record that ends Added with the model's name and the page link, and the list still
  shows it after reloading the page, with no banner.
- A scanned PDF with a text-only backend ends Couldn't read with the scan message and the Settings link, and the
  banner does not say "Couldn't reach your AI". A connection failure still does.
- An upload queued while the model downloads becomes Added after the scheduler's next pass.
- A record stuck in Reading shows Interrupted; a held duplicate and a flagged page show their own status.
- Another person's records are not shown; a file name containing HTML is escaped; the list holds at most ten rows.
- Tests: `tests/test_upload_status.py` (model-free).
