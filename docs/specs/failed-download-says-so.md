# Spec: a failed model download says so, in plain words, with a Try again

Status: implemented. Lane: `pillar:model`. Issue: #330.

## Problem

A failed model download was silent, and then looked like a success. Measured with a simulated failed pull (a fake
`ollama pull` that exits with an error; no real download), 2026-11-20: `/models/pull-status` returned `pulling: ""`, no
progress and no error field; the previous model kept serving (correct); but the `/models` page script treated "the download
stopped" as "finished" and showed "[the previous model] finished downloading and is now your local model", then redirected
to a "selected" page. Settings showed a similar "finished downloading and is now ready" banner.

## Requirements

1. A download that ends without success and was not cancelled is recorded as failed for that account (`_MODEL_PULL_FAILED`:
   the tag and a reason, `""` or `disk`) until a new download starts or one succeeds. Nothing the download tool printed is
   stored or shown; only whether the output said the disk is full.
2. `/models/pull-status` returns `failed`: `null`, or `{tag, reason}`.
3. `/models` and Settings (`/personalize`) show, on load and when a running download ends: "The download did not finish.
   Nothing was changed and your current model keeps working." with a Try again button (a form post to the existing
   `/models/pull` with the same tag). For a full disk: "There was not enough free disk space to finish the download. Free
   some space, then try again." The "finished downloading" banner is not shown for a failed download.
4. The message never contains engine or version text, a tag that needs a newer engine, an error dump or any output of the
   download tool.
5. The pull path itself (the command, progress parsing, cancel, activation on success) is unchanged.

## Acceptance

- With a simulated failed pull: the status reports `failed`, both pages render the failure message and the retry form, the
  previous model stays selected, and none of the tool's output (for example a 412 or "newer version" text) appears.
- With a successful pull `failed` is `null` and the failure banner stays hidden; a new download clears the last failure; a
  cancelled download records none; a full disk gives the disk sentence (`tests/test_failed_download_says_so_330.py`).
