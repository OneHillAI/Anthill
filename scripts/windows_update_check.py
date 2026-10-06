#!/usr/bin/env python3
"""Install an old build of Anthill on Windows and check that it updates itself to a newer one.

    python scripts/windows_update_check.py OLD-setup.exe NEW-setup.exe NEW-setup.exe.sig --new-version 0.9.2

The two installers are built by the update job of the Windows CI with a throwaway signing key. The old one is built to ask
a local update feed (http://127.0.0.1) and trusts that key. This script serves the feed, then:

1. installs the old build silently (the installer starts the app);
2. requires the app to ask the feed, download the new installer, and be replaced by the new version;
3. requires the new version to start, answer, and be the only copy running;
4. closes it normally and requires nothing to be left, then uninstalls.

It is the automatable proof of the spec's "updates itself to the next version". Exit status 0 means every step passed.
Windows only; the manifest and version helpers are plain functions so they are tested on every platform.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installer_check as base  # noqa: E402 - the shared Windows helpers live beside this script


# ── plain helpers (no Windows needed) ────────────────────────────────────────────────────────────


def build_manifest(version: str, installer_url: str, signature: str) -> dict:
    """The update manifest the app reads (the same shape Tauri's release action writes)."""
    entry = {"signature": signature.strip(), "url": installer_url}
    return {
        "version": version,
        "notes": "update check",
        "pub_date": "2026-01-01T00:00:00Z",
        "platforms": {"windows-x86_64": entry, "windows-x86_64-nsis": entry},
    }


def version_matches(reported: str, expected: str) -> bool:
    """Whether a file's reported version is ``expected``; Windows often adds a fourth number (0.9.2.0)."""
    return re.fullmatch(re.escape(expected) + r"(\.0+)*", reported.strip()) is not None


class Feed:
    """A local update feed: serves the manifest and the new installer, and remembers what was asked for."""

    def __init__(self, folder: Path, port: int):
        self.folder = folder
        self.port = port
        self.requests: list[str] = []
        outer = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (the name the base class calls)
                outer.requests.append(self.path)
                super().do_GET()

            def log_message(self, *args):  # keep the job log readable
                pass

        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), functools.partial(Handler, directory=str(folder))
        )

    def start(self) -> Feed:
        threading.Thread(target=self._server.serve_forever, daemon=True, name="update-feed").start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def asked(self, name: str) -> bool:
        return any(path.split("?")[0].endswith("/" + name) for path in self.requests)


# ── Windows side ─────────────────────────────────────────────────────────────────────────────────


def file_version(path: Path) -> str:
    """The product version stamped in an executable, through PowerShell."""
    command = f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion"
    return base._run(["powershell", "-NoProfile", "-Command", command]).stdout.strip()


def launch_logged(app_dir: Path, app_exe: str, log_path: Path) -> subprocess.Popen:
    """Start the app with its output in a file: it has no console, and its update and startup errors go to stderr."""
    log = log_path.open("w", encoding="utf-8", errors="replace")
    return subprocess.Popen([str(app_dir / app_exe)], cwd=str(app_dir), stdout=log, stderr=subprocess.STDOUT)


def show_app_log(app: subprocess.Popen | None, log_path: Path) -> None:
    exit_code = app.poll() if app else None
    print(f"--- the app we started: {'still running' if exit_code is None else f'exited with {exit_code}'} ---", flush=True)
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    print(text[-3000:] or "(no output)", flush=True)


def main(old: str, new: str, signature_file: str, new_version: str, port: int = 8765) -> int:
    local = Path(os.environ["LOCALAPPDATA"])
    ok = True
    folder = Path(tempfile.mkdtemp(prefix="anthill-feed-"))
    installer_name = Path(new).name
    shutil.copy(new, folder / installer_name)
    manifest = build_manifest(
        new_version,
        f"http://127.0.0.1:{port}/{installer_name}",
        Path(signature_file).read_text(encoding="utf-8"),
    )
    (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    feed = Feed(folder, port).start()
    app_exe = "anthill-desktop.exe"
    app_dir: Path | None = None
    try:
        # 1. install the old build; the installer starts it, and it asks the feed straight away
        result = base._run([old, "/S"])
        app_dir = base.wait_for(lambda: base.find_installed_app(local), 120)
        ok &= base.report("installs the old build", bool(app_dir), f"exit {result.returncode}; {app_dir}")
        if not app_dir:
            base.diagnostics()
            return 1
        app_exe = base.main_exe_name(app_dir) or app_exe

        # The installer starts the app itself. Stop that copy and start our own, so its output is captured.
        base.stop_everything(app_exe)
        base.wait_for(base.nothing_left, 20)
        app_log = folder / "app.log"
        app = launch_logged(app_dir, app_exe, app_log)

        # 2. it must ask the feed, download the new installer, and be replaced
        asked = base.wait_for(lambda: feed.asked("latest.json"), 90)
        ok &= base.report("the app asks the update feed", bool(asked), str(feed.requests))
        if not asked:  # nothing more can follow; show why instead of waiting out the other steps
            show_app_log(app, app_log)
            base.diagnostics(app_exe, app_dir)
            return 1
        downloaded = base.wait_for(lambda: feed.asked(installer_name), 120)
        ok &= base.report("the app downloads the new installer", bool(downloaded))
        replaced = base.wait_for(lambda: version_matches(file_version(app_dir / app_exe), new_version), 240)
        ok &= base.report("the app is replaced by the new version", bool(replaced), file_version(app_dir / app_exe))
        if not replaced:
            show_app_log(app, app_log)
            base.diagnostics(app_exe, app_dir)
            return 1

        # 3. the new version must run: the installer relaunches it, or we start it and say so
        if not base.wait_for(lambda: base.pids(app_exe), 60):
            print("windows-update-check: INFO - the installer did not relaunch the app; starting it", flush=True)
            base.start_app(app_dir, app_exe)
        serving = base.wait_for(base.backend_serving, 150)
        ok &= base.report("the new version starts and answers", bool(serving), f"port {serving}")
        ok &= base.report("only one copy of the app is running", len(base.pids(app_exe)) == 1, str(base.pids(app_exe)))
        if not serving:
            base.diagnostics(app_exe, app_dir)

        # 4. close it normally; nothing may be left; then uninstall
        ok &= base.report("the new version's window is shown", bool(base.wait_for(lambda: base.window_title_shown(app_exe), 60)))
        gone = base.close_politely(app_exe, window_wait=0)
        ok &= base.report("nothing is left running after a normal close", gone)
        if not gone:
            base.diagnostics(app_exe, app_dir)
        base.stop_everything(app_exe)
        uninstaller = app_dir / base.UNINSTALLER_EXE
        if uninstaller.is_file():
            base._run([str(uninstaller), "/S"])
            ok &= base.report("uninstalls", bool(base.wait_for(lambda: not (app_dir / app_exe).exists(), 60)))
    finally:
        feed.stop()
        base.stop_everything(app_exe)
        shutil.rmtree(folder, ignore_errors=True)
    print(f"windows-update-check: {'ALL PASSED' if ok else 'FAILED'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if sys.platform != "win32" or len(args) != 5 or args[3] != "--new-version":
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], args[2], args[4]))
