# Keep SHA-pinned GitHub Actions current with Dependabot version updates

Lane: `pillar:platform`. Priority: medium (hygiene, not urgent: pins are frozen, not broken). Category: enhancement.
Full spec, added by the PR that builds this: `docs/specs/dependabot-actions-updates.md`.
Issue #232. Follows #177 and PR #228, which pinned every action by commit SHA.

## Why

After #228 every `uses:` in `.github/workflows/` is a full commit SHA with a version comment: 111 pinned uses of 16 distinct
actions across 27 workflow files. A pin stops a moved tag from changing what a job runs, but it also never moves on its
own, and nothing proposes a newer version:

- The repo has Dependabot **security** updates enabled and no `.github/dependabot.yml` (checked on `main`), so there are
  no version-update PRs for any ecosystem.
- The pins are already behind. Pinned versus the latest release reported by the GitHub releases API on 2026-11-17:
  `actions/checkout` v4.2.2 vs v7.0.1 (33 call sites on that one pin), `actions/setup-python` v5.6.0 vs v7.1.0 (21),
  `actions/setup-node` v4.4.0 vs v7.1.0, `actions/upload-artifact` v4.6.2 vs v7.0.2, `actions/download-artifact` v4.3.0 vs
  v8.0.2, `actions/create-github-app-token` v1.12.0 vs v3.3.0 (it handles the bot's App key), `docker/login-action` v3.7.0
  vs v4.8.0, `softprops/action-gh-release` v2.6.2 vs v3.0.4. Within-major updates (the part that carries fixes) are
  invisible today.
- Without an update path a pin gets a security fix only when a person notices and edits up to 33 lines by hand.

Prior decisions: #228 deliberately did not add this config because it starts a stream of PRs, and left it for the
maintainer to decide. #210 already lets Dependabot PRs pass intake (`machine_authors`).

## What changes

- Add `.github/dependabot.yml` with a single `github-actions` entry for `/`:
  - schedule: weekly, on Monday;
  - one group for **minor and patch** updates, so every non-breaking update arrives as ONE PR a week;
  - **major** updates are not proposed for now (`ignore` for `version-update:semver-major`); majors need a person
    (new Node runtime, renamed inputs) and are taken in a deliberate pass;
  - `open-pull-requests-limit: 5`;
  - `cooldown` of 7 days, so a release is not adopted until it is a week old;
  - `commit-message` prefix `ci` with the scope, so headlines stay Conventional Commits (`ci(deps): ...`);
  - `ignore` `dtolnay/rust-toolchain`: it is pinned to a `master` commit by design (its README requires a commit in
    `master`'s history, and #228 passes `toolchain: stable` explicitly); it is bumped by hand.
- A test, `tests/test_dependabot_config.py`, pins the config's shape so it cannot drift.
- A short section in `docs/asdd-anthill-process.md` on how pin updates arrive and who reviews them.
- The spec `docs/specs/dependabot-actions-updates.md` and this change folder.

## Guardrails (do NOT touch)

- No version updates for `cargo`, `npm` or `pip`: those stay on security updates only. Turning them on would open a PR per
  dependency.
- No pinned SHA changes in the PR that builds this. The first Dependabot PR does the bumping.
- No major-version bumps proposed (see above).
- No change to branch protection, intake, the changelog and test gates, or the "Sync bot PRs with main" workflow.
- Dependabot never merges anything: every PR still needs the required checks and a named human review (`.github/**` is a
  protected path).

## Constraints and prior decisions

- Dependabot PRs change only `.github/workflows`, so the changelog and test-presence gates (#211, #213) do not apply and
  intake passes through the machine-author waiver (#210). Protected-path review does apply: one PR a week for the
  maintainer to review.
- Dependabot PRs are not covered by "Sync bot PRs with main", so each goes BEHIND `main` after every merge and needs a
  manual "Update branch" until that workflow is extended. That is a separate issue and out of scope here.
- `dtolnay/rust-toolchain` must stay on a `master` commit with `toolchain: stable`.
- Every pin must keep a trailing `# version` comment (`tests/test_workflow_hygiene.py`). Dependabot is expected to rewrite
  the SHA and that comment together for a SHA-pinned action; the first PR confirms it.
- The first run is the largest: grouping, the PR limit and the cooldown bound it.

## Verification

- GitHub's Dependabot page shows the configuration as valid, with no errors.
- The first scheduled run opens at most one grouped minor/patch PR, none for a major, and none for `rust-toolchain`.
- That PR passes intake and `tests/test_workflow_hygiene.py` (every action still pinned by SHA, every pin still commented),
  and re-running the pin audit from #228 on it shows each version comment is a tag on its commit.
- `tests/test_dependabot_config.py` fails if the config gains another ecosystem, loses the group, allows majors, drops the
  `rust-toolchain` ignore, or loses the commit prefix.

## Impact

One extra PR a week needing maintainer review. Files: `.github/dependabot.yml` (protected path), one test, the spec, this
folder, and a paragraph in the process doc. No product code.

## Decisions (answered on #232)

1. Weekly, grouped: Monday, all minor and patch updates in one PR.
2. Major updates ignored for now; taken in a deliberate pass.
3. A 7-day cooldown (GitHub Actions supports `default-days` only; security updates are never delayed).
4. Other ecosystems stay on security updates only.
