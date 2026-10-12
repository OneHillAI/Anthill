"""Guards for the desktop window's size handling (docs/specs/desktop-window-size.md).

CI does not run `cargo test --lib`, so these read the config and the Rust source for the rules the Rust unit
tests also check where cargo runs. Each assertion is a rule a change could silently break: removing the launch
snapshot, treating an unreadable file as "no file", returning early from the exit undo, inverting a "prepared"
check, maximizing on every launch, dropping the once-per-process guard, marking the window prepared anywhere
but at the end of `prepare_window` or when the restore failed, showing the window off the main thread, or
letting a second launch show the window before it was prepared. They read the source's shape, so they catch
these edits, not every possible wrong program; the decisions themselves are pure functions with Rust tests.

All of this is macOS only. On Windows and Linux the shell must stay exactly as it was before this change (fixed
creation size, no window-state plugin, no launch snapshot, no exit undo, the old show and second-instance code),
so the guards also check that the plugin dependency and every piece of the window-size code sit under
`cfg(target_os = "macos")`, and that the code a non-macOS build compiles mentions none of it.

The shell keeps its window hidden until the local backend answers and shows it itself, so the config must say
`visible: false`, and the window must not be created maximized (a window created maximized and then hidden can
flash or take focus on some platforms; the shell maximizes the hidden window just before it shows it, on a
first launch). The size the window-state plugin saves and restores must stay SIZE | POSITION | MAXIMIZED: never
VISIBLE (it would show the splash early), FULLSCREEN or DECORATIONS (restoring them calls set_fullscreen on
every launch while hidden, and the plugin would store the fullscreen frame as the normal size).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TAURI = Path(__file__).resolve().parent.parent / "src-tauri"
SOURCE = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
# The shipped code only: everything before the test modules.
SHIPPED = SOURCE[: SOURCE.index("#[cfg(all(test, unix))]")]

MACOS = '#[cfg(target_os = "macos")]'
NOT_MACOS = '#[cfg(not(target_os = "macos"))]'
MACOS_OR_TEST = '#[cfg(any(target_os = "macos", test))]'


def _strip_comments(code: str) -> str:
    """Drop `//` line comments (and `///` docs), keeping a `://` inside a URL string."""
    return re.sub(r"(?<!:)//[^\n]*", "", code)


def _fn(name: str) -> str:
    """The text of a top-level `fn name...` item, from its signature to its closing brace, without comments."""
    match = re.search(rf"\nfn {name}\b.*?\n\}}\n", SOURCE, re.S)
    assert match, f"fn {name} not found in src-tauri/src/lib.rs"
    return _strip_comments(match.group(0))


def _body(name: str) -> str:
    """The statements of a top-level fn, without its signature and closing brace, whitespace-normalised."""
    code = _fn(name)
    inner = code[code.index("{") + 1 : code.rstrip().rindex("}")]
    return " ".join(inner.split())


def _non_macos_view() -> str:
    """The shipped code as a non-macOS build compiles it, without comments: every item, block, statement or match
    arm under `cfg(target_os = "macos")` (or `cfg(any(target_os = "macos", test))`) is dropped, and the
    `cfg(not(target_os = "macos"))` markers go."""
    lines = _strip_comments(SHIPPED).split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line in (MACOS, MACOS_OR_TEST):
            i += 1
            depth, opened = 0, False
            while i < len(lines):
                code = lines[i]
                depth += code.count("{") - code.count("}")
                opened = opened or "{" in code
                i += 1
                if (opened and depth <= 0) or (not opened and code.rstrip().endswith(";")):
                    break
            continue
        if line != NOT_MACOS:
            kept.append(lines[i])
        i += 1
    return " ".join("\n".join(kept).split())


def _attrs_before(marker: str) -> str:
    """The attribute lines directly above the single line that starts with `marker` in the source."""
    match = re.search(rf"((?:[ ]*#\[[^\n]*\]\n)*)[ ]*{re.escape(marker)}", SOURCE)
    assert match, f"{marker!r} not found in src-tauri/src/lib.rs"
    return match.group(1)


def _main_window() -> dict:
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    return next(w for w in conf["app"]["windows"] if w["label"] == "main")


def test_the_main_window_is_hidden_and_not_created_maximized():
    window = _main_window()
    assert window["visible"] is False
    assert not window.get("maximized")
    assert not window.get("fullscreen")


def test_the_window_state_flags_are_size_position_and_maximized_only():
    code = _fn("window_state_flags")
    assert "StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED" in code
    for banned in ("all()", "VISIBLE", "FULLSCREEN", "DECORATIONS"):
        assert banned not in code
    assert ".with_state_flags(window_state_flags())" in _fn("window_state_plugin")


def test_the_doc_comments_sit_above_the_functions_they_describe():
    assert re.search(
        r'///[^\n]*\n(///[^\n]*\n)*#\[cfg\(target_os = "macos"\)\]\nfn window_state_flags', SOURCE
    )
    flags_doc = SOURCE[: SOURCE.index("fn window_state_flags")].rsplit("\n\n", 1)[-1]
    assert "What the window-state plugin saves and restores" in flags_doc
    assert "VISIBLE" in flags_doc
    plugin_doc = SOURCE[: SOURCE.index("fn window_state_plugin")].rsplit("\n\n", 1)[-1]
    assert plugin_doc.startswith(
        "/// The window-state plugin, as the app and the ACL tests both build it."
    )
    assert "saves and restores" not in plugin_doc


def test_the_plugin_does_not_restore_the_hidden_window_when_it_is_created():
    # Restoring a maximized state on the hidden window when it is created can show it or take focus before the
    # backend answers. The shell restores it itself, in prepare_window, just before it shows the window.
    assert '.skip_initial_state("main")' in _fn("window_state_plugin")


def test_prepare_window_runs_once_restores_then_maximizes_only_on_a_first_launch():
    body = _body("prepare_window")
    expected = (
        "use tauri_plugin_window_state::WindowExt; "
        "if PREPARE_STARTED.swap(true, Ordering::SeqCst) { return; } "
        "let saved = has_saved_main_state(LAUNCH_STATE.get().and_then(LaunchState::bytes)); "
        "let restored = window.restore_state(window_state_flags()); "
        "if !saved { let _ = window.maximize(); } "
    )
    assert body.startswith(expected), body


def test_the_window_is_marked_prepared_only_at_the_end_of_prepare_window_and_only_if_the_restore_worked():
    # An exit saves the window as it is; until prepare_window has restored it that is the unrestored 1200 x 820,
    # and the exit undo only runs while the flag is false. A failed restore leaves the flag false.
    body = _body("prepare_window")
    assert body.endswith(
        "if restored.is_ok() { WINDOW_PREPARED.store(true, Ordering::SeqCst); }"
    ), body
    assert body.index("restore_state(") < body.index("WINDOW_PREPARED.store(")
    shipped = _strip_comments(SHIPPED)
    assert shipped.count("WINDOW_PREPARED.store(") == 1  # nowhere else
    for other_write in (
        "WINDOW_PREPARED.swap(",
        "WINDOW_PREPARED.fetch_",
        "WINDOW_PREPARED.compare_",
    ):
        assert other_write not in shipped


def test_the_window_is_prepared_and_shown_on_the_main_thread():
    # restore_state holds the plugin's state mutex while it calls window getters; off the main thread each one
    # waits for the main thread, so a quit then hangs the app. Both ways of showing the window go through here.
    on_main = (
        r"run_on_main_thread\(move \|\| \{ prepare_window\(&shown\); let _ = shown\.show\(\); \}\)"
    )
    for name in ("show_on", "show_startup_error"):
        body = _body(name)
        assert re.search(on_main, body), name
        assert body.count("prepare_window(") == 1, name
        # the prepare-and-show closure is the macOS block; the only other show is the plain one Windows and Linux
        # (and mobile) keep, with nothing to prepare
        assert MACOS + " { let shown = window.clone();" in body, name
        assert body.index("prepare_window(") < body.index(NOT_MACOS), name
        shows = [m.start() for m in re.finditer(r"\.show\(\)", body)]
        assert len(shows) == 2, name
        after = body[body.index(NOT_MACOS) + len(NOT_MACOS) :]
        assert re.match(r" (let _ = window\.show\(\);|window\.show\(\)\?;)", after), name
        assert "prepare_window" not in after, name
    assert _strip_comments(SHIPPED).count("prepare_window(&") == 2  # only from those two


def _single_instance_registrations() -> list[tuple[str, str]]:
    """Each `{ builder = builder.plugin(tauri_plugin_single_instance::init(...)); }` block with its cfg line."""
    found = re.findall(
        r"\n    (#\[cfg\([^\n]*\)\])\n    \{\n        builder = builder\.plugin\("
        r"tauri_plugin_single_instance::init\((.*?)\n        \}\)\);\n    \}\n",
        SOURCE,
        re.S,
    )
    assert len(found) == 2, found
    return [(cfg, " ".join(_strip_comments(code).split())) for cfg, code in found]


def test_the_second_instance_callback_never_shows_an_unprepared_window():
    callbacks = dict(_single_instance_registrations())
    callback = callbacks[MACOS]
    assert re.search(
        r"run_on_main_thread\(move \|\| \{ "
        r"if !second_instance_may_show\(WINDOW_PREPARED\.load\(Ordering::SeqCst\)\) \{ return; \} ",
        callback,
    ), callback
    assert callback.index("second_instance_may_show(") < callback.index(".show()")
    assert _body("second_instance_may_show") == "prepared"


def test_the_launch_snapshot_treats_only_a_missing_file_as_no_file():
    from_read = re.search(r"fn from_read\(.*?\n    \}\n", SOURCE, re.S)
    assert from_read, "LaunchState::from_read not found"
    code = " ".join(_strip_comments(from_read.group(0)).split())
    assert "Ok(bytes) => LaunchState::Present(bytes)," in code
    assert "Err(err) if err.kind() == std::io::ErrorKind::NotFound => LaunchState::Missing," in code
    assert "Err(_) => LaunchState::Unreadable," in code
    remember = _body("remember_launch_window_state")
    assert "LaunchState::from_read(std::fs::read(path))" in remember
    assert "None => LaunchState::Unreadable" in remember
    assert ".ok()" not in remember


def test_an_exit_before_the_window_is_prepared_puts_the_saved_state_back():
    # The plugin saves on exit (before our callback runs). An updater restart or a quit during the boot wait
    # would otherwise overwrite the saved state with the unrestored 1200 x 820 window.
    exit_arm = re.search(
        r"\n( *#\[cfg\([^\n]*\)\]\n)? *tauri::RunEvent::Exit => \{.*?\n            \}", SOURCE, re.S
    )
    assert "undo_unprepared_window_save(app_handle);" in exit_arm.group(0)
    assert exit_arm.group(1).strip() == MACOS
    undo = _body("undo_unprepared_window_save")
    # the decision comes first, from the live flag and the launch snapshot, with nothing returning before it
    assert undo.startswith(
        "let action = unprepared_exit_action(WINDOW_PREPARED.load(Ordering::SeqCst), LAUNCH_STATE.get()); "
        "let Some(path) = window_state_path(app) else { return; }; "
    ), undo
    assert undo.count("return") == 1
    assert "ExitAction::Write(bytes) => std::fs::write(path, bytes)," in undo
    assert "ExitAction::Remove => std::fs::remove_file(path)," in undo
    assert "ExitAction::Leave => Ok(())," in undo
    # the decision itself: a prepared window's save is kept, an unreadable launch file is never touched
    decide = _body("unprepared_exit_action")
    assert decide.startswith("if prepared { return ExitAction::Leave; } match launch {"), decide
    assert "Some(LaunchState::Present(bytes)) => ExitAction::Write(bytes)," in decide
    assert "Some(LaunchState::Missing) => ExitAction::Remove," in decide
    assert "Some(LaunchState::Unreadable) | None => ExitAction::Leave," in decide


def test_the_saved_state_at_launch_is_remembered_in_setup():
    setup = re.search(r"\.setup\(\|app\| \{.*?\n        \}\)", SOURCE, re.S).group(0)
    setup = " ".join(_strip_comments(setup).split())
    assert MACOS + " remember_launch_window_state(app.handle());" in setup
    assert "LAUNCH_STATE.set(" in _body("remember_launch_window_state")


def _cargo_sections() -> dict[str, list[str]]:
    """Cargo.toml's table headers mapped to their non-comment lines (no TOML parser: Python 3.10 has none)."""
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in (TAURI / "Cargo.toml").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("[") else raw.strip()
        if line.startswith("["):
            current = line
            sections.setdefault(current, [])
        elif line:
            sections.setdefault(current, []).append(line)
    return sections


def test_the_window_state_plugin_is_a_macos_only_dependency():
    sections = _cargo_sections()
    holders = [
        header
        for header, lines in sections.items()
        if any(line.split("=", 1)[0].strip() == "tauri-plugin-window-state" for line in lines)
    ]
    assert holders == ["[target.'cfg(target_os = \"macos\")'.dependencies]"], holders
    assert 'tauri-plugin-window-state = "~2.4"' in sections[holders[0]]


def test_the_window_size_code_is_compiled_on_macos_only():
    for item in (
        "fn window_state_plugin",
        "fn window_state_flags",
        "static LAUNCH_STATE",
        "static WINDOW_PREPARED",
        "static PREPARE_STARTED",
        "fn window_state_path",
        "fn remember_launch_window_state",
        "fn prepare_window",
        "fn undo_unprepared_window_save",
    ):
        assert _attrs_before(item).strip() == MACOS, item
    # the pure decisions are compiled where they are used: on macOS, and for their Rust tests
    for item in (
        "enum LaunchState",
        "impl LaunchState",
        "fn has_saved_main_state",
        "enum ExitAction",
        "fn unprepared_exit_action",
        "fn second_instance_may_show",
    ):
        assert _attrs_before(item).strip().splitlines()[0] == MACOS_OR_TEST, item
    run = " ".join(_strip_comments(SHIPPED[SHIPPED.index("pub fn run()") :]).split())
    assert MACOS + " { builder = builder.plugin(window_state_plugin()); }" in run
    assert run.count("window_state_plugin()") == 1
    assert dict(_single_instance_registrations()).keys() == {
        '#[cfg(all(desktop, not(target_os = "macos")))]',
        MACOS,
    }


def test_windows_and_linux_build_the_shell_exactly_as_before():
    # What a non-macOS build compiles must not touch the window's size at all ...
    view = _non_macos_view()
    for name in (
        "tauri_plugin_window_state",
        "window_state_plugin",
        "window_state_flags",
        "prepare_window",
        "PREPARE_STARTED",
        "WINDOW_PREPARED",
        "LAUNCH_STATE",
        "LaunchState",
        "remember_launch_window_state",
        "undo_unprepared_window_save",
        "unprepared_exit_action",
        "second_instance_may_show",
        "has_saved_main_state",
        "run_on_main_thread",
        "OnceLock",
        "AtomicBool",
    ):
        assert name not in view, name
    # ... and keeps the code it had: show the window directly, stop the backend on exit, and on a second launch
    # show, unminimise and focus the window straight away.
    assert "let _ = window.show();" in view
    assert view.count("window.navigate(tauri::Url::parse(&url)?)?; window.show()?; Ok(())") == 1
    assert "tauri::RunEvent::Exit => stop_backend(app_handle)," in view
    callbacks = dict(_single_instance_registrations())
    assert callbacks['#[cfg(all(desktop, not(target_os = "macos")))]'] == (
        '|app, _args, _cwd| { if let Some(window) = app.get_webview_window("main") { '
        "let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus(); }"
    )
