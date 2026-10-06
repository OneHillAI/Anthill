# Spec: the beta release lane (Cut Beta, then Promote)

Status: R1 to R5 and R10 implemented in the first PR; R6 to R9 (Promote) in the second. Not yet proven on a real
beta or a real promotion.
Lane: `pillar:platform`
Relates to: `.github/workflows/desktop-beta.yml`, `.github/workflows/desktop-release.yml`,
`.github/workflows/release.yml`, `.github/workflows/desktop-promote.yml`, `scripts/beta_release.py`,
`src-tauri/tauri.beta.conf.json`, `docs/releasing.md`, `docs/AUTOUPDATE.md`

## 1. Problem

The repo is public and every installed app follows `releases/latest`. Today a stable tag `vX.Y.Z` builds the
signed app, publishes the release and moves the live feed and the stable download link in one step: anything
that is released is live for everyone at once, with no place to try it first. Merging a PR does not release
anything (it adds the change to `main`), so the missing piece is a lane between "merged" and "live" where a
build can be installed, used and judged, and only then shipped.

## 2. Concepts

- **Merging a PR** adds it to `main`. That is the pool of changes.
- **A beta** is a snapshot of `main` at one commit, so it contains every PR merged up to that commit.
- **Promoting** ships one tested beta to the live app. Nothing else reaches the live app.

## 3. Requirements

R1. Cut Beta SHALL be started by hand (a manually run workflow), from `main` only, for a version `X.Y.Z-rc.N`
whose base is higher than the current live version and whose N is exactly one more than the last beta of that
base (1 if none), and only on a commit whose checks on `main` (lint, the four test runs and the browser tests) are green; intake already gated the PR
before it merged, and a merge commit on `main` carries no run of it.

R2. A beta SHALL be published as a GitHub pre-release `vX.Y.Z-rc.N`, never as the latest release. The beta
apps' update feed SHALL be the rolling pre-release `beta-channel` (it holds `latest.json`). The live feed
(`releases/latest`) and the stable `Anthill.dmg` download SHALL NOT be touched by a beta.

R3. The beta app SHALL have its own identity: product name "Anthill Beta", bundle id
`org.onehill.anthill.beta`, its own update feed, and its own data folder (the backend keeps its data under the
app's product name), so it installs next to the live app and cannot open the live app's database or keys. It
is signed with the same key. Its displayed and updater version is the rc number, patched into the checkout and
never committed. The live app's data folder SHALL be unchanged. The app's secrets (JWT and encryption keys) live in
that data folder (`secrets.env`), not in the macOS keychain, so the separate folder isolates them; a test fails if
code starts using the keychain, so the isolation is revisited if that ever changes.

R4. Today's stable release workflows SHALL fire only on a stable tag (`vX.Y.Z`), so a beta tag can never start
a live release, and each SHALL also refuse to run at all unless the tag is exactly `vX.Y.Z`, as its first step,
so the protection does not depend on how GitHub matches tag filters.

R5. Cut Beta SHALL offer a dry run that builds and signs without creating a tag, a release or a feed, so the
build is proven before anything is published.

R6. (Promote) A beta SHALL be promoted only as tested. If any other change has merged to `main` since the beta
commit, other than release files (the changelog, version bumps, assembled changelog fragments), the beta is out
of date and a new beta must be cut and tested. Promote SHALL refuse otherwise. Of the release files, `pyproject.toml` and `anthill/__init__.py` may change only
their version line (they could otherwise carry a dependency or code change under a version bump), and the beta's
release must hold what Cut Beta publishes (the update manifest and a signed updater bundle), so a hand-made tag
cannot be promoted.

R7. (Promote) Promote SHALL also require: an approval in the `production` environment, an owner (listed
in `.github/release-owners.txt`, under `.github/` so changing the list needs the code owner's review) as the person who
starts it, a passing verdict from the test agent on the beta commit (the `asdd/test` commit status its workflow records,
which must be `success` and set by the workflow's bot; a structured signal, not text read from a comment), green
required checks on the commit it releases,
the changelog assembled for that version, and no existing stable tag for it. It then pushes the stable tag so
that today's two release workflows run unchanged. Promote defaults to a dry run, which runs every check and
creates nothing.

R8. No agent SHALL be able to promote: the approval and the owner check are mechanical, not a convention.

R9. A change found wrong in a beta is fixed forward (a fix PR) or reverted (a revert PR), through the normal
gates; a new beta is cut and tested, and the live app receives only that net tested result. "Change A without
change B" is only possible by reverting B first, because a beta contains everything merged.

R10. A beta that fails part-way (for example during notarization) can leave a published pre-release that no live
or beta app follows, because the feed is updated last. Nothing is deleted automatically (an automatic delete
could race a concurrent run or remove something being inspected); the recovery is to cut the next beta, and
the orphan may be deleted by hand. The release tag is pinned to the commit that was built, and Cut Beta runs one
at a time so two betas can never interleave a feed update.

R11. A new stable release SHALL start as a pre-release, never as the latest release, and a release owner makes it
the latest release by hand (Edit release, untick pre-release, tick Set as the latest release) once it is complete
and checked (the verify-release script). The Release workflow publishes a release minutes before the app files
exist, and as latest it sent visitors to a download that was not there and left installed apps without an update
feed (v1.0.1, 2026-10-04, fixed by hand). The two stable workflows therefore publish a pre-release.

## 4. Acceptance criteria

- `tests/test_beta_release_lane.py` passes: the version rules, the version patching, the green-checks rule,
  the release-files-only rule, that the two stable workflows ignore a beta tag, that Cut Beta is hand-started
  and main-only and publishes only a pre-release with the beta identity, and that the live feed, the live app
  identity and the live app's data folder are unchanged.
- `tests/test_beta_promote.py` passes: who may promote, which beta is promotable, that the test agent's verdict
  must be the bot's own `asdd/test` status on that commit, and that Promote checks everything before it creates anything, defaults
  to a dry run, is hand-started from main, and only its second job (behind the approval) mints the bot token
  and creates the tag.
- The Rust unit test for the data-folder name passes (`cargo test --lib`).
- The guard step of both stable workflows is executed by the tests: it passes `vX.Y.Z` tags and refuses a beta tag
  and every other name.
- The merged beta configuration was checked by compiling the app with and without the overlay: the beta has the
  beta name, bundle id and feed and keeps the same signing key; the live build is unchanged.
- Cut Beta's workflow parses and is linted with the kit's workflow linter.
- `tests/test_stable_releases_start_as_prereleases.py` passes: both stable workflows publish a pre-release that is not
  the latest, and the release doc tells the owner how to make it latest.

## 5. Proof on a real change

Not "set up" until a beta has been cut and used: run Cut Beta with the dry run, then for real, install Anthill
Beta next to the live app, and confirm it opens its own empty data folder, shows the rc version, and updates
from the `beta-channel` feed. Promote is first run as a dry run.

## 6. Out of scope

- A feed restricted to team members (a private repository and an authenticated updater). The feed address is a
  single setting in `src-tauri/tauri.beta.conf.json`, so it can be moved later.
- Cutting a beta automatically on merge, and per-PR preview builds.
- Folding the duplicated macOS build steps of Cut Beta and the stable workflow into one reusable workflow.
