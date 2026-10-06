#!/usr/bin/env python3
"""Install the real Windows installer, run the real app, and check how it behaves, then remove it.

    python scripts/windows_installer_check.py path\\to\\Anthill_x.y.z_x64-setup.exe

This is the automatable half of the real-machine checklist (docs/releasing.md). On a clean Windows machine it:

1. installs the NSIS installer silently, for the current user;
2. waits for Anthill (the installer starts it) and requires the backend to come up and answer, and the window to be shown;
3. kills the app the hard way (a crash) and requires the backend to disappear by itself;
4. starts it again, closes it the normal way, and requires nothing to be left running;
5. uninstalls, and requires the user's data to survive.

Set ANTHILL_CHECK_DATA_FOLDER to "Anthill Beta" to check the beta app. Exit status 0 means every step passed. It prints what it sees at each step so a failure can be read from the log.
Windows only; the parsing helpers are plain functions so they are tested on every platform.
"""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SIDECAR_EXE = "anthill-server.exe"
UNINSTALLER_EXE = "uninstall.exe"


# ── parsing (no Windows needed) ──────────────────────────────────────────────────────────────────


def parse_tasklist(output: str) -> list[dict[str, str]]:
    """Rows of ``tasklist /FO CSV /V`` as dicts keyed by the header line. ``INFO:`` (nothing found) is empty."""
    text = output.strip()
    if not text or text.startswith("INFO:"):
        return []
    return list(csv.DictReader(io.StringIO(text)))


def parse_listening_ports(netstat_output: str, pids: set[int]) -> list[int]:
    """Loopback or wildcard TCP ports in LISTENING state owned by one of ``pids`` (``netstat -ano -p TCP``)."""
    ports: list[int] = []
    for line in netstat_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        local, pid = parts[1], parts[4]
        if not pid.isdigit() or int(pid) not in pids:
            continue
        host, _, port = local.rpartition(":")
        if host in ("127.0.0.1", "0.0.0.0", "[::1]", "[::]") and port.isdigit():
            ports.append(int(port))
    return sorted(set(ports))


def find_installed_app(local_app_data: Path) -> Path | None:
    """The folder a per-user install put the app in: the one holding the bundled backend, ``anthill-server.exe``.

    Looked for in the places the installer is known to use and one level under them, so a change of Tauri's
    default folder does not need a change here."""
    for parent in (local_app_data / "Programs", local_app_data):
        if not parent.is_dir():
            continue
        for candidate in (parent, *sorted(p for p in parent.iterdir() if p.is_dir())):
            if (candidate / SIDECAR_EXE).is_file():
                return candidate
    return None


def main_exe_name(app_dir: Path) -> str | None:
    """The desktop shell's executable in the install folder: the program that is neither the backend nor the
    uninstaller (it is named after the Rust package, not the product)."""
    others = sorted(p.name for p in app_dir.glob("*.exe") if p.name.lower() not in (SIDECAR_EXE, UNINSTALLER_EXE))
    return others[0] if others else None


# ── Windows side ─────────────────────────────────────────────────────────────────────────────────


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kwargs)


def processes(image: str) -> list[dict[str, str]]:
    return parse_tasklist(_run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/V"]).stdout)


def pids(image: str) -> set[int]:
    return {int(row["PID"]) for row in processes(image)}


def listening_ports() -> list[int]:
    return parse_listening_ports(_run(["netstat", "-ano", "-p", "TCP"]).stdout, pids(SIDECAR_EXE))


def wait_for(condition, timeout: float, step: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(step)
    return condition()


def answers(port: int) -> bool:
    try:
        # A bare GET of the login page; localhost only, redirects count as an answer.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=3) as response:  # noqa: S310
            return response.status in (200, 302, 303)
    except urllib.error.URLError:
        return False
    except OSError:
        return False


def report(label: str, ok: bool, detail: str = "") -> bool:
    print(f"windows-installer-check: {'PASS' if ok else 'FAIL'} - {label}" + (f" ({detail})" if detail else ""), flush=True)
    return ok


def diagnostics(app_exe: str = "", app_dir: Path | None = None) -> None:
    print("--- diagnostics ---", flush=True)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for folder in (local, local / "Programs", app_dir):
        if folder and folder.is_dir():
            print(f"{folder}: {sorted(p.name for p in folder.iterdir())[:40]}", flush=True)
    for image in (app_exe, SIDECAR_EXE, "ollama.exe", "msedgewebview2.exe"):
        if not image:
            continue
        rows = processes(image)
        print(f"{image}: {[(r.get('PID'), r.get('Window Title')) for r in rows]}", flush=True)
    print(_run(["netstat", "-ano", "-p", "TCP"]).stdout[-1500:], flush=True)


def start_app(app_dir: Path, app_exe: str) -> subprocess.Popen:
    return subprocess.Popen([str(app_dir / app_exe)], cwd=str(app_dir))


def backend_serving() -> int | None:
    for port in listening_ports():
        if answers(port):
            return port
    return None


def window_title_shown(app_exe: str) -> bool:
    return any("Anthill" in (row.get("Window Title") or "") for row in processes(app_exe))


def nothing_left() -> bool:
    return not pids(SIDECAR_EXE)


def close_politely(app_exe: str, *, window_wait: float = 60.0, timeout: float = 40.0) -> bool:
    """Close the app the way the close button does, and report whether nothing is left running.

    A plain ``taskkill`` (no /F) only reaches a window that is already visible, and the app shows its window a moment
    after its backend starts answering, so one request sent too early is ignored. So wait for the window first, then
    repeat the request until the app and its backend are gone."""
    wait_for(lambda: window_title_shown(app_exe), window_wait)

    def close_and_check() -> bool:
        _run(["taskkill", "/IM", app_exe])
        return nothing_left() and not pids(app_exe)

    return bool(wait_for(close_and_check, timeout, step=3))


def stop_everything(app_exe: str) -> None:
    for image in (app_exe, SIDECAR_EXE):
        _run(["taskkill", "/F", "/IM", image])


def main(installer: str) -> int:
    local = Path(os.environ["LOCALAPPDATA"])
    # The app keeps its data under its product name: "Anthill", or "Anthill Beta" for the beta.
    data_dir = local / os.environ.get("ANTHILL_CHECK_DATA_FOLDER", "Anthill")
    ok = True

    # 1. install, for the current user, with no prompts. The installer starts the app itself when it is done.
    result = _run([installer, "/S"])
    app_dir = wait_for(lambda: find_installed_app(local), 120)
    ok &= report("installs silently for the current user", bool(app_dir), f"exit {result.returncode}; {app_dir}")
    if not app_dir:
        diagnostics()
        return 1
    app_exe = main_exe_name(app_dir)
    ok &= report("the app and its backend are installed side by side", bool(app_exe), str(app_exe))
    if not app_exe:
        diagnostics("", app_dir)
        return 1

    # 2. the app must come up: the installer started it, so give it time, and start it ourselves if it did not
    wait_for(lambda: pids(app_exe), 20)
    if not pids(app_exe):
        start_app(app_dir, app_exe)
    port = wait_for(backend_serving, 150)
    ok &= report("the backend starts and answers", bool(port), f"port {port}")
    if not port:
        diagnostics(app_exe, app_dir)
        return 1
    ok &= report("the window is shown", bool(wait_for(lambda: window_title_shown(app_exe), 30)))
    ok &= report("the app keeps its data in the data folder", (data_dir / "secrets.env").is_file(), str(data_dir))

    # 3. a crash: kill the shell the hard way; the backend must go by itself
    _run(["taskkill", "/F", "/IM", app_exe])
    ok &= report("the backend stops when the app crashes", bool(wait_for(nothing_left, 20)))
    if not nothing_left():
        diagnostics(app_exe, app_dir)
        _run(["taskkill", "/F", "/IM", SIDECAR_EXE])

    # 4. a normal close: ask the window to close; nothing must be left
    start_app(app_dir, app_exe)
    port = wait_for(backend_serving, 150)
    ok &= report("it starts again after a crash", bool(port), f"port {port}")
    if port:
        gone = close_politely(app_exe)
        ok &= report("nothing is left running after a normal close", gone)
        if not gone:
            diagnostics(app_exe, app_dir)
    stop_everything(app_exe)

    # 5. uninstall; the user's data must stay
    uninstaller = app_dir / UNINSTALLER_EXE
    ok &= report("an uninstaller was installed", uninstaller.is_file())
    if uninstaller.is_file():
        _run([str(uninstaller), "/S"])
        removed = wait_for(lambda: not (app_dir / app_exe).exists(), 60)
        ok &= report("uninstalls", bool(removed))
    ok &= report("the user's data survives the uninstall", (data_dir / "secrets.env").is_file())

    print(f"windows-installer-check: {'ALL PASSED' if ok else 'FAILED'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.platform != "win32":
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
