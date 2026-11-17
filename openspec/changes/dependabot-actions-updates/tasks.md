# Tasks

- [x] Maintainer answers the four decisions in the proposal.
- [x] Add `.github/dependabot.yml`: `github-actions`, `/`, weekly, minor and patch grouped, majors ignored, limit 5,
  7-day cooldown, `ci` commit prefix with scope, `dtolnay/rust-toolchain` ignored (each choice as decided).
- [x] `tests/test_dependabot_config.py`: only the `github-actions` ecosystem, the group present, majors ignored,
  `rust-toolchain` ignored, a PR limit set, the commit prefix set; mutation-test each assertion.
- [x] Confirm against GitHub's documentation and the published Dependabot JSON schema that each key used (`groups`,
  `ignore`, `cooldown`, `commit-message`) is supported and spelled as written; the sources are recorded in the spec.
- [x] `docs/asdd-anthill-process.md`: how pin updates arrive and who reviews them.
- [x] `docs/specs/dependabot-actions-updates.md` and the `openspec/changes/` folder.
- [ ] Local gates (`gates.py`) pass; open the PR as the bot with `Fixes #N`, only after the maintainer says yes.
- [ ] After merge: check the Dependabot page for config errors, review the first grouped PR, and confirm it updated each
  SHA and its version comment together.
