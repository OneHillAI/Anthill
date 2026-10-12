"""Guards for the desktop window's size handling (docs/specs/desktop-window-size.md).

CI does not run `cargo test --lib`, so these read the config and the Rust source for the rules the Rust unit tests
also check where cargo runs.

The shell keeps its window hidden until the local backend answers and shows it itself, so the config must say
`visible: false`, and the window must not be created maximized (a window created maximized and then hidden can
flash or take focus on some platforms; the shell maximizes the hidden window just before it shows it, on a
first launch). The size the window-state plugin saves and restores must stay SIZE | POSITION | MAXIMIZED: never
VISIBLE (it would show the splash early), FULLSCREEN or DECORATIONS (restoring them calls set_fullscreen on
every launch while hidden, and the plugin would store the fullscreen frame as the normal size). The Rust unit
test `the_window_state_flags_never_include_visible_fullscreen_or_decorations` checks the same where cargo runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TAURI = Path(__file__).resolve().parent.parent / "src-tauri"


def _main_window() -> dict:
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    return next(w for w in conf["app"]["windows"] if w["label"] == "main")


def test_the_main_window_is_hidden_and_not_created_maximized():
    window = _main_window()
    assert window["visible"] is False
    assert not window.get("maximized")
    assert not window.get("fullscreen")


def test_the_window_state_flags_are_size_position_and_maximized_only():
    source = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    body = re.search(r"fn window_state_flags\(\)[^{]*\{(.*?)\n\}", source, re.S)
    assert body, "window_state_flags() not found in src-tauri/src/lib.rs"
    code = body.group(1)
    assert "StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED" in code
    for banned in ("all()", "VISIBLE", "FULLSCREEN", "DECORATIONS"):
        assert banned not in code
    assert ".with_state_flags(window_state_flags())" in source


def test_the_plugin_does_not_restore_the_hidden_window_when_it_is_created():
    # Restoring a maximized state on the hidden window shows and activates it on Windows, before the backend
    # answers. The shell restores it itself, in prepare_window, just before it shows the window.
    source = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    plugin = re.search(r"fn window_state_plugin<.*?\n\}", source, re.S).group(0)
    assert '.skip_initial_state("main")' in plugin
    prepare = re.search(r"fn prepare_window<.*?\n\}", source, re.S).group(0)
    assert prepare.index("restore_state(") < prepare.index("maximize()")


def test_an_exit_before_the_window_is_prepared_puts_the_saved_state_back():
    # The plugin saves on exit (before our callback runs). An updater restart or a quit during the boot wait
    # would otherwise overwrite the saved state with the unrestored 1200 x 820 window.
    source = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    exit_arm = re.search(r"tauri::RunEvent::Exit => \{.*?\n            \}", source, re.S).group(0)
    assert "undo_unprepared_window_save(" in exit_arm
    undo = re.search(r"fn undo_unprepared_window_save<.*?\n\}", source, re.S).group(0)
    assert "WINDOW_PREPARED.load(" in undo and "LAUNCH_STATE" in undo


def test_the_startup_error_window_is_prepared_like_the_normal_one():
    source = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    error = re.search(r"fn show_startup_error\(.*?\n\}", source, re.S).group(0)
    assert error.index("prepare_window(") < error.index("window.show()")


def test_the_first_launch_maximizes_the_hidden_window_just_before_showing_it():
    source = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    show_on = re.search(r"fn show_on<.*?\n\}", source, re.S).group(0)
    assert show_on.index("prepare_window(") < show_on.index("window.show()")
    assert "window.maximize()" in source
