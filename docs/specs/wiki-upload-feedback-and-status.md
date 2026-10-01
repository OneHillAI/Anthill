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

**3. Real diagnosis instead of a rhetorical question.** A new `_ingest_failure_reason(backend)`
helper calls the backend's `health()` method (already existed on `OllamaBackend`, just never
surfaced here - a live `GET /api/tags` check, not reachability by assumption) when a document
fails to ingest, and threads the specific result through as `?reason=`. `wiki.html`'s `error=ingest`
banner shows that reason verbatim (e.g. `` Ollama not reachable at http://localhost:19999. Is
`ollama serve` running? ``) instead of the old generic "is the model running?" - answering the
question instead of asking the user to answer it themselves. `health()` is duck-typed
(`getattr(backend, "health", None)`) and the helper never raises, since a diagnostic that crashes
the error path would replace one bug with a worse one.

**4. The Dashboard pill stops going stale.** `dashboard.html`'s poll loop used to `return` without
rescheduling once it saw `ready: true`, so one good check early in a session could leave "Running"
displayed forever even if the model later stopped. It now reschedules itself every 30s indefinitely
once ready (instead of stopping), and a fetch failure against the status endpoint itself now also
flips the pill to "Preparing" rather than leaving the last-known state untouched - the pill always
reflects a live check, not a memory of one.

## Verification

Live-verified end-to-end against a real browser, a real local Ollama instance, and the founder's
own installed model (`qwen3.5:9b`) - no mocks:

- **Success path:** uploaded a real file via the File API (native OS file dialogs aren't
  scriptable here); banner read exactly `"Anthill Upload Live Check" added to this wiki - indexed
  by your machine. View the page →`; the link resolved to the real, model-written page.
- **Failure path:** pointed the test org's `ollama_url` at an unreachable port (no mock, no
  stubbed exception - a real failed connection) and re-uploaded; banner read exactly `Ollama not
  reachable at http://localhost:19999. Is \`ollama serve\` running? Try again.` instead of the old
  generic question.
- **Dashboard staleness:** loaded the Dashboard against the real, healthy Ollama instance, confirmed
  the pill read "Running", then watched network traffic for 35+ seconds and confirmed
  `/local-model/status` kept being polled past the first `ready: true` response (previously it
  would have stopped after exactly one call).
- Full suite: 2825 passed, 5 skipped (non-browser) + 22 passed (browser/Playwright), including the
  existing `tests/browser/test_wiki_upload_progress.py`. `ruff check` / `ruff format` clean.

New test `tests/browser/test_wiki_upload_progress.py::test_upload_submit_shows_in_progress_feedback`
covers gap 1 (the synchronous `onsubmit` DOM effect, both the no-file no-op and the real-file case).
Gaps 2-4 were verified live per above rather than by a new automated test, since they depend on a
real local model's actual reachability - exactly the thing under test.
