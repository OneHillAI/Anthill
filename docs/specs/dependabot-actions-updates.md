# Spec: Keep SHA-pinned GitHub Actions current with Dependabot version updates

Status: implemented. Lane: `pillar:platform`. Issue: #232. Follows #177 and #228.

## Problem

After #228 every `uses:` in `.github/workflows/` is pinned by full commit SHA with a same-line `# version` comment: 111
pinned uses of 16 distinct actions across 27 workflow files. A pin stops a moved tag from changing what a job runs, but
it never moves on its own, and nothing proposed a newer version:

- The repo had Dependabot security updates and no `.github/dependabot.yml`, so there were no version-update PRs.
- GitHub documents that Dependabot security alerts are not created for actions pinned to a SHA ("Secure use reference"),
  so the existing security updates would not notice a vulnerable pinned action either.
- The pins were already behind. The latest releases reported by the GitHub releases API on 2026-11-17, against our pins:
  `actions/checkout` v7.0.1 (pin v4.2.2, 33 call sites), `actions/setup-python` v7.1.0 (v5.6.0, 21),
  `actions/create-github-app-token` v3.3.0 (v1.12.0, the bot's App key), `actions/download-artifact` v8.0.2 (v4.3.0),
  `docker/login-action` v4.8.0 (v3.7.0), `softprops/action-gh-release` v3.0.4 (v2.6.2).

## Requirements

1. `.github/dependabot.yml` SHALL configure Dependabot version updates for the `github-actions` ecosystem only, on `/`,
   weekly on Monday. No other ecosystem SHALL be configured: `cargo`, `npm` and `pip` stay on security updates only.
2. All minor and patch updates SHALL arrive as one grouped PR (`patterns: ["*"]`, `update-types` `minor` and `patch`).
3. Major updates SHALL NOT be proposed (`dependency-name: "*"` with `version-update:semver-major`).
4. A release SHALL not be adopted until it is 7 days old (`cooldown.default-days: 7`). Version updates only; security
   updates are never delayed.
5. `dtolnay/rust-toolchain` SHALL be ignored. It is pinned to a `master` commit with `# master`, has no release to
   update to, and GitHub documents that a SHA with no associated tag is moved to the latest commit, so Dependabot
   would move it. It is bumped by hand, with `toolchain: stable` kept (#228).
6. At most 5 version-update PRs SHALL be open at once, and headlines SHALL be `ci(deps): ...` (`commit-message`
   prefix `ci`, `include: scope`).
7. Every pinned action whose comment is not a version SHALL be on the ignore list, and every ignored action SHALL still
   be in use; every SHA pin SHALL keep a same-line `# version` comment, because Dependabot rewrites the SHA and that
   comment together. `tests/test_dependabot_config.py` and `tests/test_workflow_hygiene.py` enforce this.
8. The config makes no change to a pinned SHA. The first Dependabot PR does.

## Decisions

The four open questions in #232, answered there:

1. **Weekly, grouped.** One review a week; monthly would leave a fixed action unfixed for weeks.
2. **Majors ignored for now.** The first run would otherwise open a major bump for 12 actions at once (checkout v4
   to v7, create-github-app-token v1 to v3, download-artifact v4 to v8 and so on). Majors change the Node runtime or
   rename inputs and need a person; they are taken as a deliberate pass.
3. **7-day cooldown.** GitHub applies 3 days by default to version updates; 7 means a compromised or broken release is
   likely pulled before it reaches us. GitHub Actions supports only `default-days`, not the `semver-*-days` keys.
4. **Other ecosystems on security updates only.**

## What was verified, and how

- The keys and values were read against GitHub's "Dependabot options reference" and related pages (the options
  reference was verbatim for the first part and a summary for the rest) and, independently, checked against the
  published Dependabot JSON schema (SchemaStore `dependabot-2.0`): every key and every allowed value is valid, and the
  same check fails on a wrong key or value. That check names and enums only; it is not a full type validation.
- GitHub documents that Dependabot updates the version comment when it is on the same line as the SHA, which is how the
  pins are written.

## Known limits

- Not run against GitHub's Dependabot service here. Three things the documentation does not state are confirmed by the
  first run: that `dependency-name` for an action is written `owner/repo` (Dependabot's own PR titles name actions that
  way), the exact headline produced by prefix plus scope (the existing Dependabot PRs read `chore(deps): Bump ...`), and
  how a SHA with a `# v5`-style comment is classified for the semver ignore. GitHub's Dependabot page shows config errors.
- Dependabot PRs change only `.github/**`, a protected path: each needs the required checks and a named human review.
  They pass intake as a machine author (#210) and the changelog and test gates do not apply (no product code).
- Dependabot PRs are not covered by "Sync bot PRs with main", so each goes behind `main` after every merge and needs a
  manual "Update branch". Extending that workflow is a separate issue.
- Pins freeze an action; with this config they move only through a reviewed PR.

## Acceptance

- GitHub's Dependabot page shows the configuration with no errors.
- The first scheduled run opens at most one grouped minor and patch PR, none for a major, none for `rust-toolchain`.
- That PR passes intake and `tests/test_workflow_hygiene.py`, and the pin audit from #228 shows each version comment is a
  tag on its commit.
- `tests/test_dependabot_config.py` fails if the config gains another ecosystem, adds majors to the group, drops the major
  or `rust-toolchain` ignore, shortens the cooldown, adds an unsupported cooldown key, changes the commit prefix, or raises
  the PR limit above 5; it also fails if a SHA pin loses its comment or an unversioned pin is not ignored.
