# Spec: a stalled upload ends in a clear answer, with a visible status and a retry

Status: implemented. Lane: `pillar:knowledge`. Issue: #148. Extends: [wiki-upload-feedback-and-status.md](wiki-upload-feedback-and-status.md) (in-flight feedback, gap 1).

## Problem

A 360-byte Markdown note sat on "Uploading..." for 5 minutes, then failed (#148). The page had promised "up to a
minute". Three things caused it:

- The model call used the backend timeout, `Config.timeout` = 300 s (`ANTHILL_TIMEOUT`), and a text source had no
  other limit (only PDFs have a deadline).
- Text ingest set no generation cap, so one runaway generation could use all 300 s.
- The page showed one static line. It could not say how long it had waited, what it was waiting for, or that it had
  given up. The failure was the generic "Couldn't reach your AI" banner, and the person had to pick the file again.

The maintainer decided: 60 seconds should be enough, with a button to extend the limit, and a status bar must show what is
going on.

## Requirements

1. A text source (anything that is not a PDF) gives each model call `INGEST_MODEL_TIMEOUT_SECONDS` = 60 s when the caller
   asks for it with `ingest_model_timeout()`. The web upload does. A call that runs past it raises `IngestTimedOut`.
   Without the context manager nothing changes: the CLI, the inbox watcher and the first-run upload queue keep the
   backend's own timeout, because nobody is watching a spinner there. PDFs keep their 10 minute budget unchanged.
2. Each text call to Ollama is capped at `num_predict` = 1,500 tokens, per call and so per chunk, never per document, and
   sent with `think=False`. The cap bounds a runaway. It does not bound time: a slow CPU makes 1,500 tokens take longer
   than 60 s, and the timeout covers that. 1,500 tokens is about 6,000 characters. The source chunk is 10,000
   characters, and a summary fed back into a later reduction round is cut to 4,000 characters, so the cap never
   removes text the pipeline would have kept. `think=False` is needed with the cap: a reasoning model spends the same
   budget on its thinking and would return an empty page.
3. When the limit is hit, the upload writes nothing and records a `QueuedUpload` row with status `timed_out`, pointing at
   the immutable copy `preserve_raw_source` already keeps under the wiki's `raw/`. No second copy is made. The wiki page
   then says what happened ("did not finish reading that document within 60 seconds, so nothing was added") and lists
   each timed-out file with a **Try again with a longer limit** button.
4. The button posts to `POST /wiki/upload/retry/{id}`, which re-runs that file with `INGEST_EXTENDED_TIMEOUT_SECONDS` =
   300 s, from the raw copy, without choosing the file again. It goes through the same `_ingest_and_propose`: same
   backend, same `propose_upload` dedup and review hold, same scope. It looks the row up by id, org and the signed-in
   user, so another user's id and an unknown id get the same answer (`?error=retrymissing`) and the other user never
   sees the button. It re-checks edit permission for the row's scope, and refuses a stored path that is not directly
   inside that scope's `raw/`. A second timeout says so (`?error=ingesttimeout_long`) and keeps the button. Success
   marks the row `done`. Choosing the same file again and succeeding also clears its button.
5. The upload form sends with an XHR and shows a status bar (`role="status"`, a native `<progress>`): "Uploading N%" from
   the browser's own upload progress, then, once the server has the whole file, "Your AI is reading the document and
   writing the page: N s so far. It gives up on a step after 60 s." with the bar running against the limit. Past the
   limit it says long documents take several steps, each allowed 60 s. A PDF says it can take minutes and that Anthill
   stops at 10 minutes. The previous result banner is hidden when a new upload starts. All status text is set with
   `textContent`.
6. The server's answer is still the redirect, so every end state (added, held, queued, timed out, error) is the existing
   banner on the reloaded page. If the connection drops, the server answers with an error, or 12 minutes pass, the bar
   ends in a visible message and the button is released.
7. The connector import has no status bar or retry, so it keeps the 300 s limit it had.

## Known limits

- The bar cannot tell reading from writing. The upload is one request, and the server only answers at the end. Showing
  those steps needs a progress endpoint and a hook inside `ingest()`, which is a protected path. The bar shows only what
  the browser knows: bytes sent, then time since the server had the file.
- The limit is per model call, not per upload. A long text is read in several calls, so the whole upload can pass 60 s
  and the bar says so. A text has no overall deadline (PDFs do), so the 12 minute browser stop is the last resort.
- A timeout stops Anthill waiting, not Ollama. It may keep generating, so a retry straight away can queue behind it.
- There is no cancel button. Closing the tab stops the page waiting, not the server.
- Image sources are not PDFs, so they also get 60 s per call. A slow machine may need the longer retry.
- Reasoning models now run with thinking off for ingest, so a summary no longer includes a thinking pass.
- A `timed_out` row stays until the file goes through. The raw copy is kept in the Files tab either way.
- The unexplained `400` after each successful ingest in the issue is not addressed here.

## Acceptance

- A backend whose call outlasts the limit ends the upload in `?error=ingesttimeout` within the limit, writes no page, and
  leaves a `timed_out` row that holds the original bytes.
- The wiki page then names the limit and shows the retry button for the owner only.
- The retry, with a fast backend, adds the page without a file, uses the 300 s limit, and is held by dedup when the same
  file is already in the wiki. A retry by another user, for an unknown id, or with a path outside `raw/` does nothing.
- A text budget sets the per-call timeout and restores the backend's own; a PDF budget keeps its own deadline.
- Text calls to Ollama carry `num_predict` and `think=False` on every chunk; PDF calls do not.
- The page's script has the status bar, uses `textContent` and never `innerHTML`, and the "up to a minute" line is gone.
- Tests: `tests/test_wiki_upload_timeout.py` (model-free) and `tests/browser/test_wiki_upload_progress.py`.
