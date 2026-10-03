# Releasing Anthill: beta first, then live

Who this is for: the maintainers. What it covers: how a change gets from `main` to the live app, and how a bad
change is handled. The design and the rules are in
[`specs/beta-release-lane.md`](specs/beta-release-lane.md); the updater itself is in [`AUTOUPDATE.md`](AUTOUPDATE.md).

## The idea

Merging a PR adds it to `main`; it does not release anything. A **beta** is a snapshot of `main` at one commit, so
it contains every PR merged up to then. **Promoting** ships one tested beta to the live app. Only a promotion
reaches the live app.

## The steps

1. **Merge PRs as usual.** Nothing reaches users.
2. **Cut a beta.** Actions, **Cut Beta**, Run workflow. Type a version `X.Y.Z-rc.N` (the next number above the
   live version, and N one more than the last beta). Tick **dry run** the first time: it builds and signs and
   creates nothing. Then run it for real.
3. **Install "Anthill Beta"** from the beta's release page (a pre-release). It sits next to the live app, keeps
   its own data folder, and updates itself to each new beta.
4. **Test it.** After a commit merges, the test agent reports PASS or FAIL on the merged PR and records its verdict on
   the commit itself (a status called `asdd/test`); Promote reads that status. If a beta's commit has none (it
   merged before the agent recorded one), run **ASDD test** on that commit by hand and wait for it. Try the beta
   by hand too.
5. **Cut the release.** Merge a release-cut PR as today: assemble the changelog (`scripts/build_changelog.py`) and
   bump the version. Only release files may change after the beta (see below).
6. **Promote.** Actions, **Promote Beta**, Run workflow, with the beta's tag (for example `v1.1.0-rc.2`). It starts
   as a **dry run**: it runs every check and creates nothing. When it passes, run it again with dry run
   unticked; it then waits for your approval in the `production` environment and creates the stable tag. The
   existing **Release** and **Desktop release** workflows run on that tag and the live app updates.

## The rule: a beta ships only as tested

If any other code changes on `main` after the beta was cut, the beta is out of date: cut a new one and test it.
Promote refuses unless the only files that changed since the beta commit are release files (the changelog,
version bumps and the assembled changelog fragments). So what you tested is exactly what ships.

## When a change turns out to be wrong

Nothing broken reaches live users, because they only ever get a promoted beta.

- **Fix forward:** a fix PR, then a new beta (`rc.N+1`), test, promote. Live users get the net result.
- **Revert:** a PR that removes the bad change, then a new beta. Use this if the fix will take a while.
- **Hold:** do not promote; keep fixing.

A beta contains everything merged, so "change A without change B" is only possible by reverting B first.

If something slips through to live, the same path is the fix, just faster. Apps that already updated do not
downgrade by themselves.

## If the stable release fails after Promote

Promote only creates the stable tag; the **Release** and **Desktop release** workflows do the rest. If one of them
fails after the tag exists, fix the cause and re-run that workflow from the Actions page. Do not create the tag
again.

## If a beta fails part-way

The beta feed is updated last, so a half-finished run leaves at most a pre-release that no app follows. Nothing is
deleted automatically. Cut the next beta; delete the orphan by hand if you like.

## One-time setup

In GitHub: Settings, Environments, New environment `production`, and add yourself as a required reviewer. That is
the second click. The release owners who may start Promote are listed under `release_owners` in `.asdd.yml`.
The signing and notarization secrets the stable release already uses are used by the beta too.

## Good to know

- The beta feed is one setting, `src-tauri/tauri.beta.conf.json`; moving it (for example to a private feed for
  team members only) does not change the app.
- Pre-releases on a public repo are visible to anyone. The beta contains only code that is already public.
- Do not edit a beta release in the GitHub page to mark it as the latest release.
