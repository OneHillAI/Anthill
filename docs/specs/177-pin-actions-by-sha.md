# Spec: Every GitHub Action is pinned by full commit SHA

Status: implemented. Lane: `pillar:platform`. Issue: #177 (the pinning part; the actionlint and zizmor step is not here).

## Problem

54 `uses:` references across 14 workflows were on a mutable tag or branch (`@v4`, `@v5`, `@stable` and so on). A tag
can be moved by whoever controls the action's repository, so a compromised or careless release changes what our
jobs run without any change here. The exposed jobs include the ones with the most authority: three use
`actions/create-github-app-token`, which mints a token from the bot's App key (`asdd-automerge`, `desktop-promote`,
`docs-deploy`); the release, desktop and image workflows publish artifacts and containers with `GITHUB_TOKEN`; and
`dtolnay/rust-toolchain@stable` is a branch, so it moved on every push to it. `tests/test_workflow_hygiene.py` froze
the count at 54 (#205) so it could only go down; this takes it to zero.

## Requirements

1. Every `uses:` in `.github/workflows/` that names a remote action SHALL be a full 40-character commit SHA followed
   by a `# <version>` comment saying which release it is.
2. Each SHA SHALL be the commit the tag pointed at when this was written, confirmed two independent ways (the GitHub
   API with annotated tags dereferenced, and `git ls-remote` against the remote), and SHALL exist on GitHub.
3. WHERE an action was already pinned elsewhere in the repo, the same SHA SHALL be reused, so each action runs one
   version everywhere: `setup-python` v5.6.0, `create-github-app-token` v1.12.0, `softprops/action-gh-release` v2.6.2,
   and `checkout` v4.2.2 (used in 33 places already).
4. `dtolnay/rust-toolchain` SHALL be pinned to a commit in the history of its `master` branch, with
   `toolchain: stable` passed explicitly on every use. The action reads the toolchain from the ref name, which a SHA
   loses, and at `master` the input is required. It SHALL NOT be pinned to the `stable` branch tip: the action's own
   README warns that commits outside `master`'s history are eventually garbage-collected and the workflow then fails.
5. Nothing else in any workflow SHALL change: no step, input, condition or ordering. This was checked by loading every
   workflow as YAML before and after and comparing the structure.
6. `tests/test_workflow_hygiene.py` SHALL fail on any action pinned to a tag or branch (its baseline is now empty), on
   a SHA pin without a version comment, and on a `rust-toolchain` use that is not SHA-pinned or has no `toolchain`.

## Decisions

- **`checkout` reuses v4.2.2, not the current `v4` tip (v4.3.1).** The repo already pins `11bd719` in 33 places and a
  `v4.3.1` pin in 3. Eight jobs that tracked the moving `v4` now run 4.2.2 like the rest. Same major version, no
  behaviour the workflows use differs, and it keeps one reviewed version for the bulk of the repo.
- **Every other pin is the tag's commit at the time of writing**, so those jobs run what they ran, now frozen.
- **No `dependabot.yml` is added.** Pinned SHAs do not update themselves, and the repo only has Dependabot security
  updates. A `github-actions` entry would open a PR per update; with the machine-author waiver (#210) those PRs can
  pass intake, but whether to take that stream is your decision.

## Known limits

- Pins freeze an action at today's release. Until an update path exists, a pin gets security fixes only when a human
  bumps it.
- Not run on GitHub: no workflow was executed. The release, desktop, Windows and image workflows run only on tags,
  schedules or dispatch, so a mistake in one would show at its next run. The structural comparison and the audit
  below are what stand in for that.
- One older pin (`checkout` `34e1148`, three uses) is commented `# v4` but is v4.3.1; left as it was.
- `actionlint` and `zizmor` (the other half of #177) are not added.

## Acceptance

- No `uses:` in `.github/workflows/` is on a tag or branch; all 54 were rewritten, 7 `rust-toolchain` uses gained
  `toolchain: stable`, and a before-and-after YAML comparison finds no other difference in any of the 14 files.
- All 17 distinct pinned (action, SHA) pairs in the repo exist on GitHub, and every version comment is a tag on its
  commit (one older comment excepted, above).
- Re-pinning one action to a tag, dropping a version comment, dropping `toolchain: stable`, or putting
  `rust-toolchain` back on `@stable` each fails a test.
- The workflow-related tests (382) pass.
