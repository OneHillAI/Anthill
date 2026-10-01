# Spec: document upload gave no feedback, and no way to know what's running

Status: implemented. Lane: `pillar:knowledge`.

## Problem

Founder report (repeated, escalating across three sessions): "I try to upload a document in the
system. It still doesn't do anything, and there's no notification to showcase that anything is in
process." Investigating turned up three compounding gaps, not one:

1. **No in-flight feedback.** `/wiki/upload` is a classic multipart `<form>` POST (not fetch/AJAX)
   whose handler reads the file and runs it through the local AI model to summarise it into a wiki
   page before ever redirecting back - genuinely slow (many seconds to over a minute) for anything
   beyond a trivial file. The page showed zero visible change during that window: no spinner, no
   disabled button, no explanatory text.
2. **No confirmation of what happened.** Once the upload *did* finish, the success banner just said
   "Document added to this wiki" - not which page, not which model read it (local machine vs. a
   connected cloud/org model), and with no link to go look at the result. A user had no way to
   confirm their file actually became something real.
3. **No way to tell what's actually running.** When ingest genuinely fails (e.g. Ollama has
   stopped since the model was last pulled), the banner asked the user a question it should have
   answered itself - "Couldn't read that document - is the model running?" - instead of checking.
   Separately, the Dashboard's model-status pill ("Running" / "Preparing") was wired from a
   one-shot JS poll that stopped rescheduling itself the moment it first saw the model ready,
   so it could carry on claiming "Running" indefinitely even after the model stopped - the exact
   thing the founder hit and asked about directly: "how shall I know what is running? As a user, I
   installed qwen3.5:9b."

Live-verified the ingest pipeline itself was never broken on current `main` (a real file through a
real local model correctly created a wiki page) - gap 1 made a working feature indistinguishable
from a broken one, and gaps 2-3 meant even a successful or a genuinely failed upload gave the user
nothing to act on.

## Fix

**1. In-flight feedback** (`wiki.html`): an `onsubmit` handler (`wikiUploadSubmit`) disables the
Upload button, changes its text to "Uploading…", and reveals a status line explaining the wait -
synchronously, before the browser navigates away on the real form submission.

**2. Confirmation names the result.** `_ingest_and_propose` (`app.py`) now returns the page's
title alongside `(applied, slug)`, and a new `_compute_where_label(cfg)` helper (shared with the
Dashboard's council card, so the two surfaces can never describe the same deployment two different
ways) reports where inference actually ran (`your machine` / `your cloud` / `the org model`). All
three upload paths (`wiki_upload`, `confirm_pdf_upload`, `wiki_import_connector`) pass
`slug`/`title`/`via` through the redirect; the banner now reads e.g. `"Anthill Upload Live Check"
added to this wiki - indexed by your machine. View the page →`, linking straight to the real page.
The review-queue case similarly names the document and links to `/wiki/review`.

**3. The local engine self-heals instead of just failing.** First pass here surfaced
`OllamaBackend.health()`'s live diagnosis (e.g. "Ollama not reachable... is `ollama serve`
running?") in the error banner instead of the old rhetorical question. The founder correctly
rejected that: "I don't care if we give the user the real reason. What should the user do with
that? ... there's a problem: it can't be uploaded, so what's the problem? We don't need to release
a fix if we didn't fix the problem." A technical reason a non-technical end user cannot act on is
not a fix - the document still fails to upload either way. The actual fix: a new
`_ensure_backend_ready(backend)` helper calls the existing `ensure_serving()` (already used
elsewhere in this codebase to auto-start the local engine before a model pull) before every ingest
attempt. Anthill bundles/manages its own local Ollama-protocol runtime
(`find_ollama_bin()` prefers the bundled binary, then a first-run download, then PATH) - a crashed,
quit, or not-yet-started local engine is Anthill's own dependency to restart, not something an end
user should ever need to know exists, let alone operate from a terminal. `_ensure_backend_ready` is
called before ingest in all three upload paths (`_ingest_and_propose`, `confirm_pdf_upload`) and in
the scheduler's queued-upload retry (`_process_one_queued_upload`) - so the common real-world cause
(the engine isn't currently running, for any reason) resolves itself with no user action and no
error shown at all. `_ingest_failure_reason(backend)` (the `health()` call from the rejected first
pass) is kept, but now only for the audit log - still useful for us to diagnose a genuine residual
failure, never shown to the user. The user-facing `error=ingest` banner, for the now much rarer case
where self-heal itself can't recover (no local engine installed anywhere, or a misconfigured URL),
is plain and actionable instead of technical: "Couldn't reach your AI to read that document. Check
your model setup, then try again." - linking to Settings rather than naming Ollama at all.

**4. The Dashboard pill stops going stale.** `dashboard.html`'s poll loop used to `return` without
rescheduling once it saw `ready: true`, so one good check early in a session could leave "Running"
displayed forever even if the model later stopped. It now reschedules itself every 30s indefinitely
once ready (instead of stopping), and a fetch failure against the status endpoint itself now also
flips the pill to "Preparing" rather than leaving the last-known state untouched - the pill always
reflects a live check, not a memory of one.

## Verification

Live-verified end-to-end against a real browser and the founder's own installed model
(`qwen3.5:9b`) - no mocks:

- **Success path (model already up):** uploaded a real file via the File API (native OS file
  dialogs aren't scriptable here) against the real, already-running local Ollama instance; banner
  read exactly `"Anthill Upload Live Check" added to this wiki - indexed by your machine. View the
  page →`; the link resolved to the real, model-written page.
- **Self-heal path (model genuinely down - the real fix):** pointed a test org's `ollama_url` at
  port 19999, confirmed via `lsof` that nothing was listening there, and set `OLLAMA_HOST` so that
  if anything spawned `ollama serve` it would bind to that same isolated port rather than the
  machine's real instance. Uploaded a real file. Confirmed via `ps`/`lsof` mid-request that a
  genuine second `ollama serve` process came up bound to port 19999 (PID distinct from the real,
  already-running instance, which was never touched and stayed reachable throughout, re-confirmed
  via `curl` afterward) - then the upload completed successfully end to end: `"Anthill Upload Live
  Check" added to this wiki - indexed by your machine. View the page →`, with the real model
  actually reading and summarising the file against the self-started instance. The spawned
  test-only instance was killed immediately after.
- **Dashboard staleness:** loaded the Dashboard against the real, healthy Ollama instance, confirmed
  the pill read "Running", then watched network traffic for 35+ seconds and confirmed
  `/local-model/status` kept being polled past the first `ready: true` response (previously it
  would have stopped after exactly one call).
- Full suite: 2827 passed, 3 skipped (non-browser, unrelated environment-dependent skips) + 22
  passed (browser/Playwright), including the existing
  `tests/browser/test_wiki_upload_progress.py`. `ruff check` / `ruff format` clean.
  `tests/test_document_upload.py::test_large_pdf_upload_requires_explicit_confirmation` was updated
  to expect the extra `_ensure_backend_ready` offload per ingest attempt.

New test `tests/browser/test_wiki_upload_progress.py::test_upload_submit_shows_in_progress_feedback`
covers gap 1 (the synchronous `onsubmit` DOM effect, both the no-file no-op and the real-file case).
Gaps 2-4 were verified live per above rather than by new automated tests, since they depend on a
real local engine's actual process lifecycle - exactly the thing under test. `ensure_serving` itself
already has unit coverage with injected boundaries (`tests/test_ollama_serving.py`); this spec adds
no duplicate of that, only the wiring into the ingest paths.

## Follow-up: a Files tab (where uploads are actually stored)

Founder, immediately after the above: "where are uploaded docs stored?" Every upload was already
preserved as an immutable raw copy under the workspace's `raw/` (`wiki/ingest.py`'s
`preserve_raw_source`, which runs for every upload, not only oversized PDFs) - but nothing in the UI
showed this. Filesystem only.

**Fix:** a new **Files** tab (`?tab=files`) alongside Pages and Principles in the wiki's sub-tab
bar (`_wiki_tabs.html`), listing that scope's raw files (name, size, modified date, newest first)
via a new `_wiki_raw_files(ws)` helper, each linking to a new `GET /wiki/raw/{name}` download route.
The route resolves `name` through `_wiki_raw_file_path`, rejecting anything that isn't a bare
filename inside that scope's `raw/` (path traversal, a symlink escape) - the same guard shape
already used for PDF-confirmation paths elsewhere in this file. Team access requires membership,
same as viewing a page; personal always resolves to the caller's own workspace, so one user can
never reach another's raw file by guessing its name.

**Verification:** `tests/test_wiki_raw_files.py` (6 tests, FastAPI TestClient, no mocks beyond the
usual fake chat backend) - the tab lists an uploaded file and shows the empty state before any
upload; a download returns the exact original bytes with a `Content-Disposition: attachment`
header; an unknown name and a `../` traversal attempt both 404; and a different org's member cannot
reach another user's personal-scope raw file by name. Also live-verified in a real browser: uploaded
a real file, confirmed it appeared on the Files tab with correct size/date, clicked through, and
confirmed via `curl` with a real session cookie that the response body matched the original file
byte-for-byte. Full suite: 2833 passed, 3 skipped + 22 browser.
