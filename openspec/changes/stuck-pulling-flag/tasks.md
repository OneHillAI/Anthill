# Tasks

- [x] Maintainer answers the decision (serialise derive and write, by saying yes to building the PR).
- [x] Regression tests for the stuck flag and the reverse order; confirmed to fail on the unfixed code.
- [x] Fix `_set_model_pulling`, the end of a download, `_cancel_model_pull` and the select route.
- [x] Whole `tests/test_local_model_settings.py` passes (40); the new and the concurrency tests pass 100 times.
- [x] `docs/specs/stuck-pulling-flag.md`, this `openspec/changes/` folder and a `changelog.d` fragment.
- [ ] Local gates (`gates.py`) pass; open the PR as the bot with `Fixes #297`, only after the maintainer says yes.
