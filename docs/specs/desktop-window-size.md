# Desktop: open filling the screen, then remember the window's size

## Problem

The desktop window always opened at a fixed 1200 x 820 and went back to that size on every launch, whatever the
screen and however the person had resized it last time. On a large display it looked small, and a resized
window was never kept.

## Scope

The shell is one codebase for macOS, Windows and Linux, so this changes all three builds. It was tested on
**macOS only** (a packaged build, see the PR text). Windows and Linux builds get the same behaviour and are
**not tested**.

## Fix

- **First launch** (no saved window state yet): the shell maximizes the window, so it fills the screen. This is
  done in code on the still-hidden window, just before it is shown, and not as `maximized` in
  `src-tauri/tauri.conf.json`: a window created maximized and then hidden can flash or take focus on some
  platforms. The config keeps `visible: false`, so the existing rule holds that nothing shows until the local
  backend answers (or a startup error is shown).
- **Later launches:** the app reopens the way it was left: size, position and whether it was maximized. This uses
  Tauri's `tauri-plugin-window-state`, saved when the window closes or the app quits, in the app's own config
  folder (the Beta app has its own, as it has its own identifier).
- **What is saved:** only size, position and maximized (`window_state_flags()` in `src-tauri/src/lib.rs`). Not
  `VISIBLE` (the shell shows the window itself, and a restored "visible" would show the splash early), and not
  `FULLSCREEN` or `DECORATIONS`: restoring those calls `set_fullscreen` on every launch while the window is
  hidden, and the plugin would then store the fullscreen frame as the normal size. Restoring full screen is not
  part of this change.
- **Saved size and the first run:** the plugin keeps the un-maximized size while the window is maximized. The
  window is created at the configured 1200 x 820, so the normal size saved after a first run is 1200 x 820, not
  the size of the screen.
- **Smaller screen:** a restored (not maximized) size is clamped to the monitor the window is shown on, so a
  size saved on a large display does not overflow a smaller one after it is disconnected. The plugin already
  ignores a saved position that is no longer on a connected screen.
- **Profile switch:** switching profile shows the window again; the first-launch maximize happens once per
  process and never on a later show.
- The dependency is held at the 2.4 line. Version 2.5 needs Tauri 2.12 and would move the whole shell off the
  2.11 line this app has been validated on; nothing else in `Cargo.lock` changes. The minimum size
  (880 x 600) is unchanged.

## Acceptance criteria

- On a fresh install the window fills the screen when it first appears, and nothing is visible before the
  backend answers.
- Resize or move the window, quit, reopen: it comes back at that size and place. Un-maximize, quit, reopen: it
  comes back un-maximized at the last size. A first run followed by un-maximizing gives 1200 x 820, not the
  screen size.
- The saved flags are exactly size, position and maximized (a unit test in Rust and a guard test in Python).
- `tauri.conf.json` has `visible: false` and does not create the window maximized (guard test).
- A restored size bigger than the monitor is shrunk to it (unit test of the clamp).
- `Cargo.lock` gains only `tauri-plugin-window-state`; no existing package changes version.
- `cargo test --lib` in `src-tauri/` passes.
