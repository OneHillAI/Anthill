"""Self-contained desktop entry point for the bundled Anthill.app.

The macOS app bundle (scripts/build-app.sh) runs THIS module via a bundled Python - no repo
clone, no system Python, no install.command. All user data lives under
``~/Library/Application Support/Anthill/`` (never a repo folder); JWT + at-rest encryption keys
are persisted there so logins survive restarts; the dashboard comes up on 127.0.0.1:8000 and
the browser opens to ``/`` - which routes a first run to personal account setup, a returning
logged-out user to sign-in, or a user with a valid session straight to the dashboard.

When run as the Tauri desktop shell's sidecar (``ANTHILL_NO_BROWSER=1``) it instead serves on a
free port, prints ``PORT=<n>`` to stdout for the shell to read, and does not open a browser - the
native Tauri window is the UI. ``ANTHILL_PORT`` forces a specific port in either mode.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

from platformdirs import user_data_dir

# Absolute imports throughout this module: it is the PyInstaller entry point (frozen as __main__ in
# both Anthill.spec and Anthill-sidecar.spec), and a frozen __main__ has no parent package, so a
# relative import (`from . import ...`) dies at startup with "attempted relative import with no known
# parent package" - which is exactly what left the Tauri sidecar dead on launch. Absolute `anthill.*`
# imports resolve both when frozen AND when this module is imported as `anthill.desktop`
# (server.py / cli.py / tests rely on that).
from anthill import profiles

HOST = "127.0.0.1"
PORT = 8000  # default for the standalone dmg launcher; sidecar/ANTHILL_PORT override it (_resolve_port)


def data_dir() -> Path:
    """Per-user data home for the installed app, resolved cross-platform via platformdirs.

    Returns ~/Library/Application Support/Anthill on macOS (byte-identical to the original
    hardcoded path, so existing installs keep their database and encryption keys),
    %LOCALAPPDATA%/Anthill on Windows, and ~/.local/share/Anthill on Linux. appauthor=False
    keeps the Windows path a single Anthill directory (no doubled author folder) and has no
    effect on macOS or Linux. The macOS-path invariant is locked by tests/test_portability.py.

    The beta app ("Anthill Beta") sets ``ANTHILL_APP_NAME`` (the Tauri shell passes its product name) so
    it keeps its OWN data home and can never open the live app's database or keys. Unset, or empty, it is
    "Anthill", so the live app and every existing install are unchanged.
    """
    name = os.environ.get("ANTHILL_APP_NAME", "").strip() or "Anthill"
    base = Path(user_data_dir(name, appauthor=False))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ensure_tls_certs() -> None:
    """Give OpenSSL a CA bundle it can actually find, so outbound TLS works in the frozen app.

    The packaged app bundles its own OpenSSL, whose compiled-in default certificate paths point at
    the build machine and do not exist on the user's Mac. Every outbound TLS handshake from the
    Python side then fails certificate verification: SMTP email (``starttls``) and HTTPS calls to
    model/provider endpoints. The failure is silent when a caller swallows it (the mailer does), so
    it looks like "email isn't configured" when in fact the send raised.

    Point OpenSSL at a real bundle: prefer ``certifi`` (bundled, cross-platform), else the OS
    bundle. Only set ``SSL_CERT_FILE`` when the operator hasn't pinned one, and skip the work
    entirely when the interpreter's own default already resolves (a normal from-source run on a
    healthy machine) so we never override a working system trust store. Never raises."""
    if os.environ.get("SSL_CERT_FILE"):
        return  # operator pinned it, or a previous call already set it
    try:
        import ssl

        paths = ssl.get_default_verify_paths()
        default_ok = bool(paths.cafile and os.path.exists(paths.cafile)) or bool(
            paths.capath and os.path.isdir(paths.capath)
        )
        # A healthy from-source interpreter already resolves; only intervene when it can't (the
        # frozen app), so a working system trust store is left untouched.
        if default_ok and not getattr(sys, "frozen", False):
            return
        cafile = ""
        try:
            import certifi

            cafile = certifi.where()
        except Exception:
            cafile = ""
        if not (cafile and os.path.exists(cafile)):
            for cand in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
                if os.path.exists(cand):
                    cafile = cand
                    break
        if cafile and os.path.exists(cafile):
            os.environ.setdefault("SSL_CERT_FILE", cafile)
    except Exception:
        pass  # best-effort: never block startup on cert discovery


def _ensure_secrets(d: Path) -> None:
    """Persist the JWT + at-rest encryption keys to the data dir so sessions and encrypted
    fields survive restarts (a fresh random key each launch would log everyone out and make
    stored secrets unreadable). Written 0600; only fills in what isn't already set."""
    env_file = d / "secrets.env"
    saved: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                saved[k.strip()] = v.strip()
    if not saved.get("ANTHILL_JWT_SECRET"):
        saved["ANTHILL_JWT_SECRET"] = secrets.token_urlsafe(48)
    if not saved.get("ANTHILL_ENCRYPTION_KEY"):
        saved["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
    env_file.write_text("".join(f"{k}={v}\n" for k, v in saved.items()))
    os.chmod(env_file, 0o600)
    for k, v in saved.items():
        os.environ.setdefault(k, v)


def _apply_paths(d: Path, *, force: bool) -> None:
    """Root every persistent-state var at ``d``. ``force=False`` respects a var the user already
    set (the normal launch); ``force=True`` overrides them (switching to a named profile, where the
    profile's paths must win even if the environment already points elsewhere).

    Per-user/team and org wikis (team tier) must be set too: their defaults are *relative*
    (data/wikis, data/org-wiki), which fail in the packaged app - its cwd is "/" (read-only), so
    opening /wiki 500s on mkdir. Point them at the data dir like everything else.
    """
    setter = os.environ.__setitem__ if force else os.environ.setdefault
    setter("ANTHILL_HOME", str(d))
    setter("ANTHILL_DB", str(d / "anthill.db"))
    setter("ANTHILL_WORKSPACE", str(d / "workspace"))
    setter("ANTHILL_FILES_DIR", str(d / "files"))
    setter("ANTHILL_SKILLS_DIR", str(d / "skills"))
    setter("ANTHILL_WIKI_ROOT", str(d / "wikis"))
    setter("ANTHILL_ORG_WIKI", str(d / "org-wiki"))


def configure_env() -> Path:
    """Point all persistent state at the active profile's data dir (not a repo). Only sets a var the
    user hasn't already overridden. Returns the data dir.

    The active profile is resolved from $ANTHILL_PROFILE (else the registry's last-used, else the
    migrated "default"), so a launcher can select a profile by setting one env var. On a plain
    single-user install the default profile *is* the data dir, so this stays byte-identical to the
    pre-profiles behaviour."""
    _ensure_tls_certs()
    d = profiles.active_data_dir(data_dir())
    _apply_paths(d, force=False)
    _ensure_secrets(d)
    _seed_skills()
    return d


def activate_profile(name: str | None = None) -> Path:
    """Force all persistent state onto a named profile and return its data dir. Unlike
    ``configure_env`` this overrides already-set path vars, so a from-source run
    (``anthill web --profile work``) lands in the profile's isolated home even if the environment
    already pointed at another. Must be called before anything imports the DB layer."""
    _ensure_tls_certs()
    d = profiles.active_data_dir(data_dir(), name)
    _apply_paths(d, force=True)
    _ensure_secrets(d)
    _seed_skills()
    return d


def _seed_skills() -> None:
    """Best-effort: drop one starter skill into the data-dir skills folder on first run so a
    fresh install isn't empty (the packaged app's skills live in the data dir, not the repo)."""
    try:
        from anthill.agent.skills import seed_example

        seed_example()
    except Exception:
        pass


def _ensure_engine_async() -> None:
    """Best-effort, in the background: make sure the local Ollama engine is up. If no Ollama is found
    (the Tauri app ships without it to keep the dmg + auto-updates small), download it once into the
    data dir, then start ``ollama serve``. Does NOT pull a model: which model to download is the
    user's choice in the first-run picker (/setup/model). Never blocks startup; silent on failure."""

    def _run() -> None:
        try:
            from anthill.inference.ollama import download_ollama, ensure_serving, find_ollama_bin

            if not find_ollama_bin():
                download_ollama()  # first run on a clean Mac: fetch the engine once into the data dir
            if not find_ollama_bin():
                return  # no engine and the download failed - the chat shows the "install Ollama" message
            ensure_serving()  # start `ollama serve` if it is installed but down
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _env_truthy(name: str) -> bool:
    """Treat an env var as a flag: set and not one of 0/false/no/empty -> True."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no")


def _should_watch_parent() -> bool:
    """Enable the orphan watchdog only in sidecar mode on POSIX. There the parent is the Tauri
    desktop shell (via the PyInstaller one-file bootloader), so a re-parent means it's gone and we
    must exit. The standalone dmg launcher (no ANTHILL_NO_BROWSER; its parent is launchd) and
    Windows (no re-parent-to-init semantics) are left untouched - the shell's own kill suffices."""
    return os.name == "posix" and _env_truthy("ANTHILL_NO_BROWSER")


def _pid_alive(pid: int) -> bool:
    """True if `pid` is a live process we could signal. ``kill(pid, 0)`` sends no signal; it just
    probes: ProcessLookupError means gone, PermissionError means alive but not ours (won't happen
    same-user). Used to watch the Tauri shell, which is our grandparent, not a child we can wait on."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _exit_when_orphaned() -> None:
    """Sidecar mode: self-terminate if the Tauri shell that launched us goes away.

    A one-file PyInstaller build runs uvicorn in a *child* of the bootloader, so there are two paths
    by which we can be orphaned and keep serving on our port - the stale ``anthill-server`` processes
    that pile up across quit/relaunch:

    * Graceful quit: the shell kills the bootloader (RunEvent::Exit), which re-parents this worker to
      launchd - caught by ``getppid()`` changing.
    * Hard crash / Force Quit: the shell is SIGKILLed, RunEvent::Exit never fires, so nothing kills
      the bootloader - it is merely re-parented and stays alive, so ``getppid()`` here does NOT
      change. To catch this we also watch the shell's own PID, which it passes as ``ANTHILL_SHELL_PID``.

    Exiting on either signal frees the port. Polling is POSIX-portable and dependency-free; ``os._exit``
    skips cleanup because uvicorn owns the main thread and the point is simply to free the port now."""
    if not _should_watch_parent():
        return
    initial_ppid = os.getppid()
    shell_pid_raw = os.environ.get("ANTHILL_SHELL_PID", "").strip()
    shell_pid = int(shell_pid_raw) if shell_pid_raw.isdigit() else 0
    if initial_ppid <= 1 and shell_pid <= 1:
        return  # nothing sane to watch (degenerate / not launched by the shell)

    def _watch() -> None:
        while True:
            reparented = os.getppid() != initial_ppid  # bootloader died (e.g. graceful-quit kill)
            shell_gone = shell_pid > 1 and not _pid_alive(shell_pid)  # shell crashed / force-quit
            if reparented or shell_gone:
                os._exit(0)
            time.sleep(1.0)

    threading.Thread(target=_watch, daemon=True, name="anthill-parent-watchdog").start()


def _free_port() -> int:
    """Ask the OS for an unused localhost port. Used in sidecar mode so the embedded backend
    never collides with another local service (e.g. an org backend already on 8000)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _resolve_port() -> int:
    """Pick the port to serve on. ANTHILL_PORT wins (explicit, either mode). In sidecar mode
    (ANTHILL_NO_BROWSER) with no explicit port, grab a free one. Otherwise keep the default 8000 -
    the standalone dmg launcher's long-standing behavior, unchanged."""
    explicit = os.environ.get("ANTHILL_PORT", "").strip()
    if explicit:
        return int(explicit)
    if _env_truthy("ANTHILL_NO_BROWSER"):
        return _free_port()
    return PORT


def _open_when_up(url: str) -> None:
    """Open the browser to the app root once the server answers; ``/`` routes to the login
    screen (or the dashboard if a session is still valid), never straight to /setup."""

    def _wait() -> None:
        import urllib.request

        for _ in range(120):
            try:
                urllib.request.urlopen(url, timeout=1)  # localhost health poll only
                break
            except Exception:
                time.sleep(1)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_wait, daemon=True).start()


def _selfcheck_office() -> int:
    """Packaged-app smoke test for file export and PDF ingestion with the BUNDLED libraries.
    Create one of each format and validate the bytes. Run against the frozen binary
    (``Anthill.app/Contents/MacOS/Anthill --selfcheck-office``) from scripts/build-app.sh, so a data
    template PyInstaller silently dropped (docx/pptx open a default template at runtime) fails the
    build here instead of a user's download. Returns a process exit code (0 = every format OK)."""
    import tempfile

    from anthill.multimodal import files

    sample = "# Anthill selfcheck\n\n- first point\n- second point\n"
    checks = {  # fmt -> expected leading magic bytes (OOXML are zips; pdf is %PDF)
        "docx": b"PK\x03\x04",
        "pptx": b"PK\x03\x04",
        "xlsx": b"PK\x03\x04",
        "pdf": b"%PDF",
    }
    ok = True
    with tempfile.TemporaryDirectory() as d:
        for fmt, magic in checks.items():
            out = Path(d) / f"selfcheck.{fmt}"
            try:
                files.create(sample, fmt, out, title="Selfcheck")
                head = out.read_bytes()[: len(magic)]
                if out.stat().st_size <= 0 or head != magic:
                    raise RuntimeError(f"bad output (size={out.stat().st_size}, head={head!r})")
                if fmt == "pdf":
                    from anthill.multimodal import read_file

                    extracted = read_file(out).text
                    if "Anthill selfcheck" not in extracted or "first point" not in extracted:
                        raise RuntimeError("PDF ingestion did not recover the fixture text")
                    print("selfcheck-office: pdf-ingest OK")
                print(f"selfcheck-office: {fmt} OK")
            except Exception as e:  # report every format, don't stop at the first failure
                ok = False
                print(f"selfcheck-office: {fmt} FAILED - {e}")
    print("selfcheck-office: " + ("ALL OK" if ok else "FAILURES - packaged data likely missing"))
    return 0 if ok else 1


def _selfcheck_cache() -> int:
    """Packaged-app check that the frozen sidecar is running the SAFE Arrow allocator and that the
    semantic cache's native stack (lancedb + pyarrow) works on a worker thread.

    Why the allocator is asserted, not just exercised: Arrow's default mimalloc allocator segfaulted
    (NULL dereference in ``mi_thread_init``) on worker threads inside the frozen sidecar, killing every
    chat turn that reached the cache. That crash depends on the real request flow and did not
    reproduce in any offline probe, so this check fails deterministically whenever the fix is not
    active in the frozen binary (``anthill/__init__.py`` selects the ``system`` pool), and also runs
    a real add/search on a worker thread with lancedb imported there, as the request path does.
    A native crash exits with a signal, so ``anthill-server --selfcheck-cache``
    (scripts/build-sidecar.sh) fails the build either way. Returns a process exit code (0 = OK)."""
    import tempfile

    result: dict[str, object] = {}

    def _work() -> None:
        try:
            import numpy as np
            import pyarrow as pa

            from anthill.cache.store import CacheStore

            result["pool"] = pa.default_memory_pool().backend_name
            with tempfile.TemporaryDirectory() as d:
                store = CacheStore(Path(d))
                vec = np.random.default_rng(7).random(1024).astype("float32")
                vec /= np.linalg.norm(vec)
                store.add("selfcheck prompt", vec, "selfcheck answer", ["slug"])
                hits = store.search(vec, 0.9)
                result["ok"] = len(hits) == 1 and hits[0].answer == "selfcheck answer"
        except Exception as e:  # report it; only a native crash escapes this
            result["error"] = f"{type(e).__name__}: {e}"

    worker = threading.Thread(target=_work, name="selfcheck-cache")
    worker.start()
    worker.join()
    pool = str(result.get("pool", "unknown"))
    if not result.get("ok"):
        print(
            f"selfcheck-cache: FAILED - {result.get('error', 'unexpected result')} (arrow pool: {pool})"
        )
        return 1
    if pool != "system":
        print(
            f"selfcheck-cache: FAILED - Arrow is using the '{pool}' allocator; the frozen sidecar must "
            "use 'system' (mimalloc segfaults on worker threads in the frozen build). Do not override "
            "ARROW_DEFAULT_MEMORY_POOL."
        )
        return 1
    print(f"selfcheck-cache: worker-thread add/search OK (arrow pool: {pool})")
    return 0


def main() -> None:
    if "--pdf-worker" in sys.argv:
        from anthill.multimodal.pdf_worker import main as pdf_worker_main

        index = sys.argv.index("--pdf-worker")
        raise SystemExit(pdf_worker_main(sys.argv[index + 1 :]))
    if "--selfcheck-office" in sys.argv:
        raise SystemExit(_selfcheck_office())
    if "--selfcheck-cache" in sys.argv:
        raise SystemExit(_selfcheck_cache())
    port = _resolve_port()
    url = f"http://{HOST}:{port}"
    # The Tauri desktop shell (and any supervisor) reads this line from our stdout to learn the
    # actual port, then points its webview at it. Harmless noise for the standalone dmg launcher.
    print(f"PORT={port}", flush=True)
    configure_env()
    _ensure_engine_async()
    if not _env_truthy("ANTHILL_NO_BROWSER"):
        # Standalone (dmg) launcher opens the system browser; in sidecar mode the Tauri window is
        # the UI, so skip it.
        _open_when_up(url)
    else:
        # Sidecar mode: tie our lifetime to the Tauri shell so we never outlive it as an orphan.
        _exit_when_orphaned()
    import uvicorn

    uvicorn.run("anthill.web.app:app", host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
