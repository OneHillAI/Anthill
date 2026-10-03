# Tasks

- [x] Spec `docs/specs/beta-release-lane.md`, with the rule that a beta is promoted only as tested.
- [x] `scripts/beta_release.py`: version rules, version patching, required-checks-green, release-files-only
  (all tested, no network).
- [x] `src-tauri/tauri.beta.conf.json`: the beta identity and feed.
- [x] The backend's data folder follows the app's product name (`anthill/desktop.py`, `src-tauri/src/lib.rs`),
  unchanged for the live app.
- [x] `.github/workflows/desktop-beta.yml` (Cut Beta, with a dry run).
- [x] The two stable release workflows fire only on a stable tag.
- [x] Tests: `tests/test_beta_release_lane.py` and the Rust unit test.
- [ ] Proof: Cut Beta dry run, then a real beta installed next to the live app (after merge).
- [x] Promote workflow (`.github/workflows/desktop-promote.yml`), its tests and the release docs `docs/releasing.md`
  (second PR).
- [ ] Proof: Promote dry run on a real beta, then a real promotion (after both PRs merge).
