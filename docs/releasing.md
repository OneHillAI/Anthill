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
   creates nothing. Then run it for real. After the macOS job it also builds the Windows alpha (see
   [Windows](#windows-alpha) below); a Windows failure never touches the macOS beta or its feed.
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
   existing **Release** and **Desktop release** workflows run on that tag and build the release.
7. **Check it, then make it latest.** The new release stays a **pre-release** while it is built, so the download
   button and the update feed keep serving the previous release. When both workflows are green and all the macOS files
   are on the release, check it (`python scripts/verify_release.py --tag vX.Y.Z --platform macos`; the stable release
   does not carry Windows yet). Then, on the release page, **Edit**, untick
   **Set as a pre-release**, tick **Set as the latest release**, and **Update release**. Only then does the live
   app update.

## Windows (alpha)

Windows is an alpha: the installer is for testers, it is not signed yet, and it is only part of the beta, not of the
stable release. Windows shows a SmartScreen warning ("More info", then "Run anyway"). The release notes of a beta that
has a Windows file say so.

- **What Cut Beta does.** After the macOS job has published the beta, a second job (`Cut Beta (Windows)`,
  `desktop-beta-windows.yml`) builds the Windows "Anthill Beta" installer, attaches it and its signed update bundle to
  the same pre-release, and adds the `windows-x86_64` entry to `latest.json`. It then installs the built beta on the
  runner and runs the install, crash, close and uninstall check, checks the release holds every Windows file
  (`scripts/verify_release.py`), and only then updates the beta feed. A failed Windows job leaves the macOS beta and its
  feed exactly as they were. The installer is also published under the fixed name `Anthill-Beta-Windows-alpha-setup.exe`
  on the `beta-channel` release.
- **Try the Windows part alone.** Actions, **Cut Beta (Windows)**, Run workflow, dry run ticked (the default): it builds
  and checks without publishing anything and needs no macOS build. Do this once after any change to the Windows build.
- **Before the first public Windows build, and after any change to the platform layer**, a person checks a real Windows
  machine and ticks this list (about 20 minutes; a machine with an NVIDIA card covers the GPU rows):
  1. Download the installer from the beta page. Windows shows the SmartScreen warning; "Run anyway" installs for the
     current user with no administrator prompt, and Anthill Beta appears in the Start menu.
  2. The window opens within about 30 seconds, is sharp at the screen's scale, resizes, shows the right icon and title,
     and no console window flashes.
  3. First run: the model picker offers models that fit the machine's memory. The local engine downloads in the
     background (about 1.5 GB); a chat answers once a model is installed.
  4. With an NVIDIA card: `ollama ps` shows the model on the GPU and the answer is clearly faster than on the CPU.
  5. Close the window: Task Manager shows no `anthill-server.exe` a few seconds later. (`ollama.exe` may stay; that is
     intended.)
  6. End the task `anthill-desktop.exe` in Task Manager: `anthill-server.exe` disappears by itself within seconds.
  7. Start again: the data and the sign-in are still there.
  8. Uninstall from Settings, Apps: the app is removed and the data folder `%LOCALAPPDATA%\Anthill Beta` stays.
  Write the result (and the Windows version and graphics card) on the beta's release page or in the issue it belongs to.

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
the second click. The release owners who may start Promote are listed in `.github/release-owners.txt` (one login per line).
The signing and notarization secrets the stable release already uses are used by the beta too.

## Good to know

- A new stable release is published as a pre-release on purpose: the Release workflow publishes it before the app
  files exist, and as the latest release it would send visitors to a missing download. Making it the latest
  release is the one step that moves the download button and the update feed, so it waits for a person who has
  checked it.
- The beta feed is one setting, `src-tauri/tauri.beta.conf.json`; moving it (for example to a private feed for
  team members only) does not change the app.
- Pre-releases on a public repo are visible to anyone. The beta contains only code that is already public.
- Do not edit a beta release in the GitHub page to mark it as the latest release.
