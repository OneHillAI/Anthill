# Tasks

- [x] Maintainer answers the decision (test only, by saying to build the PR).
- [x] Reproduce the failure deterministically with a scratch delay and record how (in the spec).
- [x] `tests/test_local_model_settings.py`: wait on the saved flag at the last read; check the four other wait spots (none needs a change).
- [x] Read the readers of `local_model_pulling`; the read-before-write gap is harmless, a second race is not and is left for its own issue.
- [x] The test passes 100 times in a loop and with the scratch delay; the whole file passes.
- [x] `docs/specs/deflake-pull-state-test.md` and this `openspec/changes/` folder.
- [ ] Local gates (`gates.py`) pass; open the PR as the bot with `Fixes #293`, only after the maintainer says yes.
