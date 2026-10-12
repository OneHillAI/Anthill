# Desktop: open filling the screen, then remember the window's size

## Problem

The desktop window always opened at a fixed 1200 x 820 and went back to that size on every launch, whatever the
screen and however the person had resized it last time. On a large display it looked small, and a resized
window was never kept.

## Scope

**macOS only.** The shell is one codebase for macOS, Windows and Linux, but everything below is compiled for macOS
alone (`cfg(target_os = "macos")` in `src-tauri/src/lib.rs`, and the plugin is a macOS-only dependency in
`src-tauri/Cargo.toml`). Windows and Linux are **unchanged and not part of this change**: their window still opens
at the fixed 1200 x 820, with no window-state plugin, no launch snapshot and no exit undo, and the shell shows the
window and handles a second launch exactly as before. Windows is not released yet and Linux is untested, so neither
gets the new behaviour. It was tested on macOS (a packaged earlier build; the final code was not re-run packaged,
see Known limits).

## Fix

- **Nothing happens to the window until the shell shows it.** The window-state plugin is built with
  `skip_initial_state("main")`, so it does not restore the hidden window when it is created (maximising a hidden
  window can show it or take focus before the backend answers). `tauri.conf.json` keeps `visible: false` and does
  not create the window maximised.
- **`prepare_window`** runs once per process on the still-hidden window, just before `window.show()`, in one step
  on the main thread (inside `run_on_main_thread`, from `show_on`, and from `show_startup_error` so an error
  window opens the same way): it calls the plugin's `restore_state` and, on a first launch, maximises the window
  so it fills the screen. Calling `restore_state` on a first launch also records the window's own 1200 x 820 as
  its normal size (the plugin keeps the normal size while maximised), so un-maximising later gives that size and
  not the screen's.
- **Main thread, and "prepared" means done.** The plugin holds its state mutex while `restore_state` calls window
  getters. Off the main thread each getter waits for the main thread, so a quit in that moment would have the main
  thread's exit save wait on the same mutex and the app would hang on quit. Running `prepare_window` and `show()`
  inside `run_on_main_thread` removes that. The "window prepared" flag (`WINDOW_PREPARED`) is set at the end of
  `prepare_window`, and only if `restore_state` returned `Ok`. If the restore failed the window is still shown,
  but the flag stays false, so the exit undo below stays armed. A separate once-flag (`PREPARE_STARTED`) keeps
  "once per process".
- **A second launch while the first is still starting** does nothing visible. The single-instance callback runs
  on the main thread and shows, unminimises and focuses the window only when the window has been prepared
  (`second_instance_may_show`, a pure function with a test); before that the window appears when the backend
  answers, as it would have anyway. A second launch during boot is dropped, not deferred.
- **First launch** means the saved file is missing, unreadable, or has no usable `main` entry (a size above zero)
  (`has_saved_main_state`, a pure function with a test). A file that exists but holds nothing for this window is
  still a first launch.
- **Later launches:** the app reopens the way it was left: size, position and whether it was maximised, saved
  when the window closes or the app quits by `tauri-plugin-window-state`, in the app's own config folder (the Beta
  app has its own, as it has its own identifier). The plugin already ignores a saved position that is on no
  connected screen.
- **What is saved:** only size, position and maximised (`window_state_flags()` in `src-tauri/src/lib.rs`). Not
  `VISIBLE` (the shell shows the window itself), and not `FULLSCREEN` or `DECORATIONS`: restoring those calls
  `set_fullscreen` on every launch while the window is hidden, and the plugin would then store the fullscreen
  frame as the normal size. Restoring full screen is not part of this change.
- **The file at launch.** In setup the shell reads the window-state file once and keeps one of three answers:
  `Missing` (the read failed with `NotFound`), `Present` (its bytes) or `Unreadable` (any other read error, or
  no config folder). Only `NotFound` counts as "no file".
- **Exit before the window is prepared.** The plugin saves on exit, before the app's own exit callback runs. An
  exit before the first show (an update that restarts the app before the backend is up, or quitting during the
  boot wait) would overwrite the saved state with the unrestored 1200 x 820 window. If the window was never
  prepared, the exit callback puts the launch bytes back, or removes the file if there was none, so the next
  launch is still a first launch. An unreadable file is never rewritten or removed by the shell's exit undo; the
  plugin's own exit save still rewrites an unreadable file whenever it is writable. The decision is
  `unprepared_exit_action`, a pure function with tests.
- **Profile switch:** switching profile shows the window again; `prepare_window` runs once per process and never
  on a later show.
- **Windows and Linux.** Every part above (the plugin registration, `prepare_window`, `PREPARE_STARTED` and
  `WINDOW_PREPARED`, the launch snapshot, the exit undo and the gated second-instance callback) is under
  `cfg(target_os = "macos")`. On other platforms the shell compiles the code it had before: `show_on` and
  `show_startup_error` call `window.show()` directly, the exit callback only stops the backend, and a second
  launch shows, unminimises and focuses the window at once. The pure decisions (`has_saved_main_state`,
  `unprepared_exit_action`, `second_instance_may_show`, `LaunchState`) are compiled on macOS and for their unit
  tests only.
- The dependency is macOS only (`[target.'cfg(target_os = "macos")'.dependencies]`) and held at the 2.4 line.
  Version 2.5 needs Tauri 2.12 and would move the whole shell off the 2.11 line this app has been validated on;
  nothing else in `Cargo.lock` changes (the lock file lists the package for every platform, but only macOS builds
  it). The minimum size (880 x 600) is unchanged.

## Known limits

- **macOS only.** Windows and Linux keep the fixed 1200 x 820 window and do not remember its size. Bringing the
  change to them is a separate change, with its own testing on those platforms.
- The Windows and Linux builds were **not built or tested** for this change. They are meant to compile exactly
  the code they had before; `tests/test_desktop_window_state.py` reads the source and checks that the code outside
  `cfg(target_os = "macos")` has none of the window-size handling and keeps the old show, exit and second-launch
  code, but no Windows or Linux build was run.
- A restored window larger than the display it opens on is left to macOS, which limits it to the screen itself:
  the shell does not resize or move it.
- No CI job runs `cargo test --lib` for `src-tauri` (adding one means editing `.github`, which needs the
  founder). The Rust unit tests run locally; `tests/test_desktop_window_state.py` runs in the normal suite and
  reads the source for the rules above. It checks the source's shape, so it catches the edits it was tried
  against (see the PR text), not every possible wrong program.
- If `restore_state` fails (which only happens if the window is gone), the window is shown but never counts as
  prepared for that session. The second-instance callback is gated on `WINDOW_PREPARED`, so a second launch does
  not bring the window forward for that session. On quit the shell's exit undo puts the file back to what it was
  at launch, so changes to the window in that session are not kept; the plugin's own exit save still rewrites an
  unreadable file whenever it is writable.
- A second launch while the first is still starting is dropped, not deferred: the window is not focused when it
  later appears.
- On macOS the window appears at its default size (1200 x 820) and takes the restored or full-screen size a
  run-loop turn later: `show()` is synchronous, while the size, position and maximise (`setContentSize`,
  `setFrameTopLeftPoint`, `zoom`) are queued asynchronously. A first frame at the default size is likely, and a
  brief resize is expected.
- An exit handled in the one run-loop turn between `show()` and the queued geometry (`WINDOW_PREPARED` is
  already true) would save the default size and not maximised. This is very unlikely, and the result is a lost
  size, not a broken file.
- `has_saved_main_state` parses the file loosely, while the plugin (2.4.1) needs every `WindowState` field and the
  whole map to parse. A hand-edited or partial file with a sized `main` entry that the plugin rejects counts as
  saved here, so there is no maximise: the window opens at its default size and the plugin overwrites the file on
  exit.
- The packaged macOS runs in the PR text were made with an earlier build that also fitted the restored window to
  the monitor's work area. That fit has since been removed, and the final code has **not** been run as a
  packaged app.

## Acceptance criteria

All of these are for the macOS app. On Windows and Linux nothing changes (guard test: the plugin is a macOS-only
dependency, and the code outside `cfg(target_os = "macos")` has no window-size handling).

- On a fresh install the window fills the screen just after it appears (on macOS a first frame at the default
  1200 x 820 is likely, then a brief resize), and nothing is visible before the backend answers.
- Resize or move the window, quit, reopen: it comes back at that size and place. Un-maximise, quit, reopen: it
  comes back un-maximised at the last size. A first run followed by un-maximising gives 1200 x 820, not the
  screen size.
- The saved flags are exactly size, position and maximised (a unit test in Rust and a guard test in Python).
- `tauri.conf.json` has `visible: false` and does not create the window maximised (guard test).
- Only `NotFound` counts as "no file" at launch; an unreadable file is never rewritten or removed by the shell's
  exit undo (unit tests). The plugin's own exit save still rewrites an unreadable file whenever it is writable.
- An exit before the window is prepared puts the launch bytes back, or removes the file if there was none; an
  exit after it keeps the plugin's save (unit tests).
- A second launch shows the window only once it has been prepared (unit test; guard test for the callback).
- Quitting before the first show leaves the saved state exactly as it was (and creates no file on a first
  launch).
- Quitting at any moment, including right after the backend answers, exits promptly and leaves a valid saved
  state (not verified on the final binary). A startup-error window opens at the restored size and quitting it
  leaves the saved state unchanged (not verified on the final binary).
- A launch saved maximised reopens maximised (not verified on the final binary).
- Opening the app a second time while the first is starting shows nothing early and starts no second backend.
- Only a file with a sized `main` entry counts as a saved state (unit test).
- `Cargo.lock` gains only `tauri-plugin-window-state`; no existing package changes version.
- `cargo test --lib` in `src-tauri/` passes on macOS.
