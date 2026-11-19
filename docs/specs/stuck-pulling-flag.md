# Spec: a late write never leaves the saved "downloading" flag stale

Status: implemented. Lane: `pillar:model`. Issue: #297. Found while fixing #293 (PR #296). Code from #200 / #202.

## Problem

`OrgSettings.local_model_pulling` ("the tag downloading, or empty") could be left set to a tag when nothing was
downloading, or cleared while a download was running.

Before this change the end of a download (`_start_model_pull()._run()` in `anthill/web/app.py`) took
`_MODEL_PULL_LOCK`, removed its entry and computed `next_tag`, left the lock, and later called
`_set_model_pulling(org_id, next_tag, ...)`. `_set_model_pulling` recomputed the state under the lock but kept the
caller's `value` whenever nothing was registered, and wrote the row outside the lock.

1. Stuck on a tag. With two overlapping downloads, A ends first and computes `next_tag = B`; before A writes, B ends and
   writes ""; A's late write stores `B`. Nothing is downloading and the flag says B. `/models/pull-status` keeps
   reporting `pulling`, the Models page keeps its banner, `/models/delete` refuses the tag (`in_use`) and the vision
   auto-download treats the install as busy. The same stale value could also come from a write that had read the state
   and was held back before it wrote.
2. Cleared while downloading. The last download reads "nothing is downloading" and is slow to write ""; a new download
   starts and writes its tag; the late "" then overwrites it.

Both were reproduced with a scratch delay on 2026-11-19 (1) and by the regression tests below on the unfixed code (both).

## Requirements

1. `_set_model_pulling` SHALL derive the value it writes from the in-memory state at the moment of writing, and the
   derive-and-write SHALL be serialised by a new `_MODEL_PULL_DB_LOCK`, taken before `_MODEL_PULL_LOCK` and never while
   holding it, so that the last write is always the one derived from the freshest state.
2. The end of a download SHALL call `_set_model_pulling(org_id, "", activate=...)`, so no value read before the lock was
   released is passed on. `value` is still honoured when no download is registered, which keeps the direct call in
   `tests/test_local_model_settings.py` (line 130) working: it sets a tag with no in-memory pull and expects it stored.
3. `_cancel_model_pull` and the "select an installed model" route, which also write the flag, SHALL derive the value
   under the same lock at the moment they write.
4. Downloads, reuse of a running download, cancel, progress and the vision auto-download rules from #200 / #202 SHALL
   behave as before. There is no schema change.

## Tests

`tests/test_local_model_settings.py` holds back the first write a pull thread makes (`_SlowPullWrites`):

- before `_set_model_pulling` is called (the caller's value is old);
- after it has read the state and before it writes (the derived value is old);
- the reverse order: the last download's "" held back while a new download starts.

Each fails on the unfixed code (the first two end with the flag stuck at `qwen3.8:27b`, the third with the flag empty
while `gemma3:12b` is running) and passes now.

## Known limits

- The lock is one per process, not per organisation. The writes it guards are short (one commit) and downloads are rare,
  so contention is negligible. It does not serialise writers in another process; as before, the in-memory state is
  per process.
- The new tests wait on injected delays of 0.5 second; they do not depend on timing to pass, only to reproduce.
- The docs-site and user guide are unchanged.

## Acceptance

- The three new tests fail on the unfixed code and pass on the fixed code; the existing concurrency test and the whole
  file pass; the new and the concurrency tests pass 100 times in a row.
- The full model-free suite passes in CI on Python 3.10 to 3.13.
