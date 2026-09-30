"""Bundled desktop entry: user data under the per-OS data dir + persisted, stable secrets."""

import os
import sys

from anthill import desktop


def test_configure_env_uses_app_support_and_persists_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in (
        "ANTHILL_DB",
        "ANTHILL_WORKSPACE",
        "ANTHILL_FILES_DIR",
        "ANTHILL_SKILLS_DIR",
        "ANTHILL_WIKI_ROOT",
        "ANTHILL_ORG_WIKI",
        "ANTHILL_HOME",
        "ANTHILL_JWT_SECRET",
        "ANTHILL_ENCRYPTION_KEY",
        "XDG_DATA_HOME",  # keep platformdirs' Linux path under the monkeypatched HOME
    ):
        monkeypatch.delenv(k, raising=False)

    d = desktop.configure_env()
    # The data dir lives under the (monkeypatched) HOME and is named after the app on every OS;
    # on macOS it must stay the Application Support path so existing installs keep their data
    # (locked by tests/test_portability.py).
    assert d.is_dir()
    assert d.name == "Anthill"
    assert tmp_path in d.parents
    if sys.platform == "darwin":
        assert d == tmp_path / "Library" / "Application Support" / "Anthill"
    assert os.environ["ANTHILL_DB"] == str(d / "anthill.db")
    assert os.environ["ANTHILL_WORKSPACE"] == str(d / "workspace")
    assert os.environ["ANTHILL_FILES_DIR"] == str(d / "files")
    # Every persistent path must live in the data dir - none may fall back to a relative default
    # (the packaged app runs with cwd="/", so a relative path fails). Regression for the /wiki 500
    # where ANTHILL_WIKI_ROOT / ANTHILL_ORG_WIKI were unset.
    assert os.environ["ANTHILL_SKILLS_DIR"] == str(d / "skills")
    assert os.environ["ANTHILL_WIKI_ROOT"] == str(d / "wikis")
    assert os.environ["ANTHILL_ORG_WIKI"] == str(d / "org-wiki")
    secrets_txt = (d / "secrets.env").read_text()
    assert "ANTHILL_JWT_SECRET=" in secrets_txt and "ANTHILL_ENCRYPTION_KEY=" in secrets_txt
    assert os.environ["ANTHILL_JWT_SECRET"] and os.environ["ANTHILL_ENCRYPTION_KEY"]


def test_secrets_are_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY", "XDG_DATA_HOME"):
        monkeypatch.delenv(k, raising=False)
    desktop.configure_env()
    first = os.environ["ANTHILL_JWT_SECRET"]
    # simulate a fresh process: clear the in-process var, re-run -> reads the SAME persisted key
    monkeypatch.delenv("ANTHILL_JWT_SECRET", raising=False)
    desktop.configure_env()
    assert os.environ["ANTHILL_JWT_SECRET"] == first


def test_resolve_port_default_is_8000(monkeypatch):
    """The standalone dmg launcher keeps serving on 8000 (unchanged)."""
    monkeypatch.delenv("ANTHILL_PORT", raising=False)
    monkeypatch.delenv("ANTHILL_NO_BROWSER", raising=False)
    assert desktop._resolve_port() == desktop.PORT == 8000


def test_resolve_port_honors_explicit_env(monkeypatch):
    """ANTHILL_PORT wins, even in sidecar mode."""
    monkeypatch.setenv("ANTHILL_PORT", "9123")
    monkeypatch.setenv("ANTHILL_NO_BROWSER", "1")
    assert desktop._resolve_port() == 9123


def test_resolve_port_sidecar_picks_a_free_port(monkeypatch):
    """Sidecar mode with no explicit port grabs a free one (so it never collides on 8000)."""
    monkeypatch.delenv("ANTHILL_PORT", raising=False)
    monkeypatch.setenv("ANTHILL_NO_BROWSER", "1")
    p = desktop._resolve_port()
    assert isinstance(p, int) and 1024 < p <= 65535


def test_free_port_is_actually_bindable():
    import socket

    p = desktop._free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", p))  # no error -> the port is genuinely free


def test_desktop_entry_uses_no_relative_imports():
    # desktop.py is the PyInstaller entry point, frozen as __main__ in both Anthill.spec and
    # Anthill-sidecar.spec. A frozen __main__ has no parent package, so ANY relative import
    # ("from . import x") dies at launch with "attempted relative import with no known parent
    # package" - which is exactly what left the packaged macOS app / Tauri sidecar dead on launch
    # once library validation stopped masking it. Keep every import in this module absolute.
    import ast
    from pathlib import Path

    tree = ast.parse(Path(desktop.__file__).read_text(encoding="utf-8"))
    relative = [
        f"line {n.lineno}: 'from {'.' * n.level}{n.module or ''} import ...'"
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.level > 0
    ]
    assert not relative, (
        "desktop.py is a frozen entry point and must use absolute imports; found relative: "
        + "; ".join(relative)
    )


def test_env_truthy(monkeypatch):
    for val in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv("ANTHILL_FLAG", val)
        assert desktop._env_truthy("ANTHILL_FLAG")
    for val in ("0", "false", "no", ""):
        monkeypatch.setenv("ANTHILL_FLAG", val)
        assert not desktop._env_truthy("ANTHILL_FLAG")
    monkeypatch.delenv("ANTHILL_FLAG", raising=False)
    assert not desktop._env_truthy("ANTHILL_FLAG")


def test_parent_watchdog_only_in_sidecar_mode(monkeypatch):
    """The orphan watchdog arms only in sidecar mode (ANTHILL_NO_BROWSER), so the standalone dmg
    launcher - whose parent is launchd, not the Tauri shell - is never made to self-exit."""
    monkeypatch.delenv("ANTHILL_NO_BROWSER", raising=False)
    assert desktop._should_watch_parent() is False
    monkeypatch.setenv("ANTHILL_NO_BROWSER", "1")
    # POSIX-only: on Windows there is no re-parent-to-init signal to watch, so it stays off.
    assert desktop._should_watch_parent() is (os.name == "posix")


def test_exit_when_orphaned_is_a_noop_outside_sidecar_mode(monkeypatch):
    """Called on every launch; in the non-sidecar (dmg) path it must start no thread and never exit."""
    import threading

    monkeypatch.delenv("ANTHILL_NO_BROWSER", raising=False)
    before = threading.active_count()
    desktop._exit_when_orphaned()  # returns immediately; must not spawn a watcher or exit
    assert threading.active_count() == before


def test_pid_alive():
    """The shell-liveness probe the watchdog uses to catch a force-quit (where getppid() can't):
    true for live processes, false once a process is gone."""
    import subprocess

    assert desktop._pid_alive(os.getpid()) is True
    assert desktop._pid_alive(1) is True  # launchd/init is always alive
    dead = subprocess.Popen(["true"])
    dead.wait()  # reaped -> its PID is now gone
    assert desktop._pid_alive(dead.pid) is False
