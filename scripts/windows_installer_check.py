#!/usr/bin/env python3
"""Install the real Windows installer, run the real app, and check how it behaves, then remove it.

    python scripts/windows_installer_check.py path\\to\\Anthill_x.y.z_x64-setup.exe

This is the automatable half of the real-machine checklist (docs/releasing.md). On a clean Windows machine it:

1. installs the NSIS installer silently, for the current user;
2. starts Anthill and requires the backend to come up and answer, and the window to be shown;
3. kills the app the hard way (a crash) and requires the backend to disappear by itself;
4. starts it again, closes it the normal way, and requires nothing to be left running;
5. uninstalls, and requires the user's data to survive.

Exit status 0 means every step passed. It prints what it sees at each step so a failure can be read from the log.
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

APP_EXE = "Anthill.exe"
SIDECAR_EXE = "anthill-server.exe"


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
    """The folder a per-user install put ``Anthill.exe`` in, under the places the installer is known to use."""
    for candidate in (local_app_data / "Programs" / "Anthill", local_app_data / "Anthill"):
        if (candidate / APP_EXE).is_file():
            return candidate
    return None


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


def diagnostics() -> None:
    print("--- diagnostics ---", flush=True)
    for image in (APP_EXE, SIDECAR_EXE, "ollama.exe", "msedgewebview2.exe"):
        rows = processes(image)
        print(f"{image}: {[(r.get('PID'), r.get('Window Title')) for r in rows]}", flush=True)
    print(_run(["netstat", "-ano", "-p", "TCP"]).stdout[-1500:], flush=True)


def start_app(app_dir: Path) -> subprocess.Popen:
    return subprocess.Popen([str(app_dir / APP_EXE)], cwd=str(app_dir))


def backend_serving() -> int | None:
    for port in listening_ports():
        if answers(port):
            return port
    return None


def window_title_shown() -> bool:
    return any("Anthill" in (row.get("Window Title") or "") for row in processes(APP_EXE))


def nothing_left() -> bool:
    return not pids(SIDECAR_EXE)


def main(installer: str) -> int:
    local = Path(os.environ["LOCALAPPDATA"])
    data_dir = local / "Anthill"
    ok = True

    # 1. install, for the current user, with no prompts
    result = _run([installer, "/S"])
    app_dir = wait_for(lambda: find_installed_app(local), 120)
    ok &= report("installs silently for the current user", bool(app_dir), f"exit {result.returncode}; {app_dir}")
    if not app_dir:
        diagnostics()
        return 1
    ok &= report("the backend is installed next to the app", (app_dir / SIDECAR_EXE).is_file())

    # 2. start it; the backend must answer and the window must be shown
    app = start_app(app_dir)
    port = wait_for(backend_serving, 150)
    ok &= report("the backend starts and answers", bool(port), f"port {port}")
    if not port:
        diagnostics()
        return 1
    ok &= report("the window is shown", bool(wait_for(window_title_shown, 30)))
    ok &= report("the app keeps its data in the data folder", (data_dir / "secrets.env").is_file(), str(data_dir))

    # 3. a crash: kill the shell the hard way; the backend must go by itself
    _run(["taskkill", "/F", "/PID", str(app.pid)])
    ok &= report("the backend stops when the app crashes", bool(wait_for(nothing_left, 20)))
    if not nothing_left():
        diagnostics()
        _run(["taskkill", "/F", "/IM", SIDECAR_EXE])

    # 4. a normal close: ask the window to close; nothing must be left
    app = start_app(app_dir)
    port = wait_for(backend_serving, 150)
    ok &= report("it starts again after a crash", bool(port), f"port {port}")
    if port:
        _run(["taskkill", "/PID", str(app.pid)])  # no /F: asks the window to close, like the close button
        gone = wait_for(lambda: nothing_left() and not pids(APP_EXE), 30)
        ok &= report("nothing is left running after a normal close", bool(gone))
        if not gone:
            diagnostics()
    for image in (APP_EXE, SIDECAR_EXE):
        _run(["taskkill", "/F", "/IM", image])

    # 5. uninstall; the user's data must stay
    uninstaller = app_dir / "uninstall.exe"
    ok &= report("an uninstaller was installed", uninstaller.is_file())
    if uninstaller.is_file():
        _run([str(uninstaller), "/S"])
        removed = wait_for(lambda: not (app_dir / APP_EXE).exists(), 60)
        ok &= report("uninstalls", bool(removed))
    ok &= report("the user's data survives the uninstall", (data_dir / "secrets.env").is_file())

    print(f"windows-installer-check: {'ALL PASSED' if ok else 'FAILED'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.platform != "win32":
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
