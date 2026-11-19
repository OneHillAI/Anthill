# A late end-of-download write can leave the saved "downloading" flag stuck with nothing downloading

Lane: `pillar:model`. Priority: medium. Category: bug.
Full spec, added by the PR that builds this: `docs/specs/stuck-pulling-flag.md`.
Issue #297. Found while fixing #293 (PR #296). Code from #200 / #202.

## Why

`local_model_pulling` could stay set to a tag with nothing downloading (the Models banner stuck, delete refused, the
vision auto-download waiting), or be cleared while a download was running, because a delayed write used a value read
earlier.

## What changes

- `_set_model_pulling` derives the value at write time under a new `_MODEL_PULL_DB_LOCK`, taken before
  `_MODEL_PULL_LOCK`.
- The end of a download passes "" so no stale value is used.
- `_cancel_model_pull` and the select-an-installed-model route derive their value under the same lock.
- Three regression tests that hold back a pull thread's write in the three places it can go wrong.

## Decision (taken by saying "yes" to building the PR)

The recommended option of #297: serialise "derive, then write" with a second lock. This also fixes the reverse order,
which the smaller option would not.

## Guardrails (do NOT touch)

- One-by-one downloads, reuse of a running download, cancel, progress and the vision auto-download rules.
- No schema change; `local_model_pulling` stays a string.
- The test fixed in #296.

## Impact

`_set_model_pulling`, `_cancel_model_pull`, the end of `_start_model_pull`, the select route in `anthill/web/app.py`;
three tests; a spec; this folder; a changelog fragment.
