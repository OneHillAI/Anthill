# Spec: stop the retired standalone build from impersonating the real Anthill.app

Status: implemented
Lane: `pillar:platform`
Relates to: `src-tauri/README.md` (the canonical Tauri shell this used to collide with),
`docs/AUTOUPDATE.md`, the June 2026 "make the Tauri build canonical" work (commit `3c57c62`,
"#2 Make the Tauri build canonical; retire the old PyInstaller dmg (#227)").

## 1. Introduction

Reported live: a locally-installed `/Applications/Anthill.app` opened Chrome to
`http://127.0.0.1:8000/` instead of showing its own native window, on a Mac where the real
Tauri-based Anthill app was expected. Investigation traced this to a real installed app being the
wrong build, not a bug in the Tauri shell's window-reveal code (which is correct - confirmed by
reading `src-tauri/src/lib.rs`'s `boot_backend`/`show_on`).

Root cause: two independent build pipelines both produce an app named `Anthill.app` with bundle
identifier `org.onehill.anthill`:

1. **The Tauri shell** (canonical, what `desktop-release.yml` publishes) - spawns a headless
   `anthill-server` sidecar with `ANTHILL_NO_BROWSER=1`, then navigates a native WKWebView window
   to it once it's up.
2. **The standalone PyInstaller build** (`Anthill.spec`, built by `scripts/build-app.sh` /
   `scripts/build-dmg.sh`, still wired into `install.command`'s "one-click installer" and
   `Makefile`'s `app`/`dmg` targets) - a single self-contained binary running
   `anthill/desktop.py` directly. Without `ANTHILL_NO_BROWSER` set (nothing in this pipeline sets
   it), `desktop.py` calls `webbrowser.open(url)` - it has no native window at all; that's its only
   and correct behavior for this build, not a defect in it.

`3c57c62` (2026-06-28) stopped CI from *publishing* the standalone dmg, making Tauri the
canonical release - but it left `Anthill.spec`/`scripts/build-app.sh`/`scripts/build-dmg.sh` fully
wired and reachable via `install.command` and `make app`/`make dmg`, still claiming the exact same
bundle name and identifier as the real release. A build from this still-live path is
indistinguishable from a real install at the filesystem/Launch-Services level until you notice the
missing window - and can silently overwrite a real install placed in the same `/Applications/Anthill.app`
path.

## 2. Requirements

### R1 - The two builds can never again claim the same identity
- THE SYSTEM SHALL give the standalone build a distinct `CFBundleIdentifier`
  (`org.onehill.anthill.devbuild`, not `org.onehill.anthill`) and a distinct bundle/display name
  (`Anthill (Dev Build)`, not `Anthill`), so Launch Services, Spotlight, and a plain `ls
  /Applications` can always tell the two apart.
- THE SYSTEM SHALL NOT let the standalone build's own scripts produce a file at `dist/Anthill.app`
  or `dist/Anthill.dmg` - those names belong to the real release's own artifacts.

### R2 - Every surface that names the standalone build says what it actually is
- WHERE `install.command`, `Makefile`, or the in-app help bot describes installing Anthill, THE
  SYSTEM SHALL make clear which build a given instruction produces, and point at
  `anthill.run/download` for the real, native, auto-updating release.

### R3 - Regression-proof
- THE SYSTEM SHALL have an automated test asserting the standalone build's identifier and name
  never re-match the Tauri shell's (`src-tauri/tauri.conf.json`'s `identifier`), and that the
  build scripts never re-target the real release's filenames.

## 3. Design

- **`Anthill.spec`**: `BUNDLE(...)`'s `name` -> `"Anthill-DevBuild.app"`, `bundle_identifier` ->
  `"org.onehill.anthill.devbuild"`, `CFBundleName`/`CFBundleDisplayName` -> `"Anthill (Dev Build)"`.
  A new header comment explains why, so a future edit doesn't quietly re-collide them.
- **`scripts/build-app.sh`**: builds/smoke-tests/reports `dist/Anthill-DevBuild.app`; header now
  states this is a dev/fallback build, not the official release, and names the real one.
- **`scripts/build-dmg.sh`**: stages/signs/notarizes/reports `dist/Anthill-DevBuild.app` /
  `dist/Anthill-DevBuild.dmg`; volume name `"Anthill (Dev Build)"`.
- **`install.command`**: the build-step message now says this produces a fallback build (no
  native window, no auto-update) and that `anthill.run/download` is the real app.
- **`Makefile`**: `app`/`dmg` target comments carry the same clarification.
- **`anthill/web/app.py`'s help-bot system prompt**: the "Install:" bullet (which told users to
  `double-click install.command; Anthill.app launches`) now correctly points at
  `anthill.run/download`'s dmg instead - it was describing a path that no longer even produces
  something named `Anthill.app`.
- **`tests/test_legacy_desktop_build_identity.py`** (new): pins `Anthill.spec`'s identifier/name
  apart from `src-tauri/tauri.conf.json`'s, and asserts none of the four build-entry files
  reference `dist/Anthill.app` / `dist/Anthill.dmg` (R3).

## 4. Tasks

- [x] `Anthill.spec` identity fields disambiguated (R1).
- [x] `scripts/build-app.sh` / `scripts/build-dmg.sh` retargeted + clarified (R1, R2).
- [x] `install.command` / `Makefile` messaging clarified (R2).
- [x] Help-bot install instructions corrected (R2).
- [x] Regression test added and verified against a revert (R3).
- [x] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **Removing the standalone build pipeline entirely.** It still serves a real purpose (testing on
  a machine without the Rust/Tauri toolchain, per `src-tauri/README.md`'s own "builds in CI, not
  locally" status note) - this spec only stops it from impersonating the real release, not
  retiring it.
- **Any change to the Tauri shell itself** (`src-tauri/`) - its window-reveal code was read and
  confirmed correct during investigation; nothing there needed fixing.
- **Fixing the specific machine this was reported on** - handled directly (swapped the wrongly-
  installed build for the genuine v0.11.20 Tauri release), not part of this repo change.
