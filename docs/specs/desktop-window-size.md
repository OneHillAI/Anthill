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

- **Nothing happens to the window until the shell shows it.** The window-state plugin is built with
  `skip_initial_state("main")`, so it does not restore the hidden window when it is created (maximizing a hidden
  window shows and activates it on Windows, before the backend answers). `tauri.conf.json` keeps
  `visible: false` and does not create the window maximized.
- **`prepare_window`** runs once per process on the still-hidden window, just before `window.show()` (in `show_on`,
  and in `show_startup_error`, so an error window opens the same way): it calls the plugin's `restore_state` and,
  on a first launch, maximizes the window so it fills the screen. Calling `restore_state` on a first launch also
  records the window's own 1200 x 820 as its normal size (the plugin keeps the normal size while maximized), so
  un-maximizing later gives that size and not the screen's.
- **First launch** means the saved file is missing, unreadable, or has no usable `main` entry (a size above zero)
  (`has_saved_main_state`, a pure function with a test). A file that exists but holds nothing for this window is
  still a first launch.
- **Later launches:** the app reopens the way it was left: size, position and whether it was maximized, saved
  when the window closes or the app quits by `tauri-plugin-window-state`, in the app's own config folder (the Beta
  app has its own, as it has its own identifier).
- **What is saved:** only size, position and maximized (`window_state_flags()` in `src-tauri/src/lib.rs`). Not
  `VISIBLE` (the shell shows the window itself), and not `FULLSCREEN` or `DECORATIONS`: restoring those calls
  `set_fullscreen` on every launch while the window is hidden, and the plugin would then store the fullscreen
  frame as the normal size. Restoring full screen is not part of this change.
- **Exit before the window is prepared.** The plugin saves on exit, before the app's own exit callback runs. An
  exit before the first show (an update that restarts the app before the backend is up, or quitting during the
  boot wait) would overwrite the saved state with the unrestored 1200 x 820 window. The shell keeps the file as
  it was at launch and, if the window was never prepared, puts it back in its exit callback, or removes it if
  there was none, so the next launch is still a first launch. A quit from the startup-error window needs no such
  guard: that window is prepared before it is shown.
- **Smaller screen:** a restored, not-maximized window is fitted to the work area of the monitor it is on (the
  area without the menu bar, Dock or taskbar), allowing for its title bar and borders, and moved inside it
  (`fit_to_work_area`, a pure function with tests). The plugin already ignores a saved position that is on no
  connected screen.
- **Profile switch:** switching profile shows the window again; `prepare_window` runs once per process and never
  on a later show.
- The dependency is held at the 2.4 line. Version 2.5 needs Tauri 2.12 and would move the whole shell off the
  2.11 line this app has been validated on; nothing else in `Cargo.lock` changes. The minimum size
  (880 x 600) is unchanged.

## Known limits

- Windows and Linux builds change too and are **not tested**. In particular, nothing yet asserts on Windows that
  no Anthill window is visible before the backend answers (`scripts/windows_installer_check.py` is not extended).
- No CI job runs `cargo test --lib` for `src-tauri` (adding one means editing `.github`, which needs the
  founder). The Rust unit tests run locally; `tests/test_desktop_window_state.py` runs in the normal suite and
  reads the config and the Rust source for the main rules.
- On macOS the system itself limits a window to the screen, so the fit to the work area is covered by unit tests
  and not seen end to end there.

## Acceptance criteria

- On a fresh install the window fills the screen when it first appears, and nothing is visible before the
  backend answers.
- Resize or move the window, quit, reopen: it comes back at that size and place. Un-maximize, quit, reopen: it
  comes back un-maximized at the last size. A first run followed by un-maximizing gives 1200 x 820, not the
  screen size.
- The saved flags are exactly size, position and maximized (a unit test in Rust and a guard test in Python).
- `tauri.conf.json` has `visible: false` and does not create the window maximized (guard test).
- A restored, not-maximized window bigger than the monitor's work area, or off it, is fitted and moved inside it
  (unit tests).
- A later launch with the state saved maximized shows nothing before the backend answers, then fills the screen.
- Quitting before the first show leaves the saved state exactly as it was (and creates no file on a first
  launch). A startup-error window opens at the restored size and quitting it leaves the saved state unchanged.
- Only a file with a sized `main` entry counts as a saved state (unit test).
- `Cargo.lock` gains only `tauri-plugin-window-state`; no existing package changes version.
- `cargo test --lib` in `src-tauri/` passes.
