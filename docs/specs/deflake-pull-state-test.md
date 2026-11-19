# Spec: the concurrent-download test waits for the saved flag it reads

Status: implemented. Lane: `pillar:platform`. Issue: #293. Test from #200 / #202.

## Problem

`tests/test_local_model_settings.py::test_concurrent_downloads_and_one_exiting_preserve_active_state` failed at random
at its last database read (`assert cfg.local_model_pulling == ""`, got `'qwen3.8:27b'`): on a push to `main`
(commit 68d5d6d, Python 3.11) and on PR #290 (Python 3.10), which only changed Markdown.

At the end of `_start_model_pull()._run()` (`anthill/web/app.py`) the pull thread takes `_MODEL_PULL_LOCK`, removes its
download from `_MODEL_PULL_STATE`, leaves the lock, and only then calls `_set_model_pulling()`, which writes
`OrgSettings.local_model_pulling`. The test finished the last fake download, waited for the in-memory state to empty and
read the saved flag at once. In the short gap between those two steps the flag still holds the old tag.

## Reproduction

With a scratch `time.sleep(0.2)` in `_run` just before `_set_model_pulling` (not committed), the unfixed test fails
every time at the same assertion on the same line. With the fix and the same delay it passes. Without the delay the fixed
test passed 100 runs out of 100, and the whole file (37 tests) passes.

## Requirements

1. The test SHALL wait, with the file's `_wait_until` helper, until the saved `local_model_pulling` is empty before it
   asserts that it is, and SHALL still assert that the progress entry is gone.
2. A helper `_pulling_flag(app_mod, org_id)` SHALL read the saved flag and close its session.
3. The assertions SHALL NOT be loosened: no sleep, no widened tolerance, no retry or `xfail` marker. If the flag never
   becomes empty the test still fails, after the 3 second wait.
4. Product code SHALL NOT change.
5. The other four `_wait_until(... _active_pulls ...)` spots in the file were read: none is followed by a read of the
   saved flag or the progress entry, so they are unchanged.

## What the gap does and does not do

The gap above is harmless: for a few milliseconds the saved flag says "downloading" after the in-memory list is empty.
The readers of the flag (the status endpoint, the delete and busy guards, the vision auto-download wait) err towards
"still downloading" for that moment.

A different interleaving found while checking this is not harmless and is NOT fixed here. If the first of two overlapping
downloads to end has its database write delayed until after the last download has written an empty flag, that late write
sets the flag back to the first download's tag, because `_set_model_pulling` only overrides a non-empty value when
something is still downloading. The flag then stays set with nothing downloading: `/models/pull-status` keeps reporting
`pulling`, the delete guard refuses that tag, and the vision auto-download treats the install as busy. This test does
NOT catch that outcome. It passes as soon as it sees the flag empty, and the late write lands afterwards: with a scratch
delay that forces the late write, the fixed test passed while the flag ended up stuck. Catching it needs the app fix and
a test of its own. It is tracked separately.

## Acceptance

- The test passes 100 times in a row without any scratch change, and passes with the scratch delay; unfixed, it fails
  with the delay.
- `tests/test_local_model_settings.py` passes as a whole; the full model-free suite passes in CI on all four Python versions.
- No product code changed.
