# Flaky test: the concurrent-download test reads the database before the pull thread has written it

Lane: `pillar:platform`. Priority: medium. Category: bug (test).
Full spec, added by the PR that builds this: `docs/specs/deflake-pull-state-test.md`.
Issue #293. Test from #200 / #202.

## Why

The test failed at random on `main` and on an unrelated docs PR: it reads the saved `local_model_pulling` right after the
in-memory state empties, but the pull thread writes the saved flag a moment later.

## What changes

In `tests/test_local_model_settings.py`, wait for the saved flag to be empty before asserting it, through a small
`_pulling_flag` helper. No product code.

## Decision (taken by saying "build the PR")

Test only, as recommended in the issue. The alternative, reordering the write in the app, is not done.

## Finding while building it (not part of this change)

A second, real race exists in `_set_model_pulling`: a delayed write from the first of two overlapping downloads to end can
set the flag back to its tag after the last download has cleared it, leaving a stuck "downloading" state. It is
reproduced with a scratch delay and is left for its own issue. The issue text called the gap harmless; that holds for the
read-before-write gap only.

## Guardrails (do NOT touch)

- Product code, the other tests, CI workflows, retry settings.
- No loosening of the assertions.

## Impact

One test file, one spec and this folder.
