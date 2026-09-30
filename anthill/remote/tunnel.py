"""Secure off-LAN remote access: expose this Anthill instance to users outside the LAN.

Provider-agnostic, mirroring the training-backend pattern: an admin picks a provider in
Settings, the app manages the tunnel, and the dashboard surfaces the public HTTPS URL.
Access stays gated by the normal login / session auth - the tunnel only carries traffic
to the same authenticated app.

Providers:
  off         no remote access (default)
  cloudflare  Cloudflare Tunnel via the `cloudflared` binary. A quick tunnel needs no
              account and prints an https://<name>.trycloudflare.com URL; a token runs a
              named tunnel with a stable hostname.
  manual      the admin runs their own reverse proxy / VPN and just records the URL.

The subprocess boundary (``spawn``) is injectable so command-building and URL-parsing are
unit-testable without running cloudflared; the only untested part is a live cloudflared run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

PROVIDERS = {
    "off": "No remote access",
    "cloudflare": "Cloudflare Tunnel (cloudflared)",
    "manual": "Bring your own reverse proxy / VPN",
}

_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9.-]*\.(?:trycloudflare\.com|[a-z0-9.-]+)\b")


def cloudflared_available() -> bool:
    """True if the cloudflared binary is on PATH."""
    return shutil.which("cloudflared") is not None


def build_cloudflared_cmd(port: int, token: str = "") -> list[str]:
    """Argv for cloudflared: a token runs a named tunnel, otherwise a quick tunnel that
    exposes the local port and prints a throwaway https URL."""
    base = ["cloudflared", "tunnel", "--no-autoupdate"]
    if token:
        return [*base, "run", "--token", token]
    return [*base, "--url", f"http://localhost:{int(port)}"]


def parse_public_url(text: str) -> str | None:
    """The first public https URL cloudflared printed (the tunnel address), or None.

    Ignores cloudflare's own api/update hosts so the user-facing tunnel URL wins.
    """
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".")
        host = url.split("://", 1)[1]
        if host.startswith(("api.", "update.", "dash.")) or host == "cloudflare.com":
            continue
        return url
    return None


class TunnelManager:
    """Owns the live tunnel subprocess + the captured public URL (in memory).

    The runtime URL is intentionally not persisted: quick-tunnel URLs change every run,
    and a stale URL is worse than none. Settings persists only the provider + token.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._url: str = ""
        self._provider: str = "off"
        self._detail: str = ""
        self._lock = threading.Lock()

    def status(self) -> dict:
        running = self._proc is not None and self._proc.poll() is None
        return {
            "provider": self._provider,
            "running": running,
            "url": self._url,
            "detail": self._detail,
        }

    def start(self, provider: str, port: int, token: str = "", *, spawn=None) -> dict:
        """Start (or reconfigure) remote access. Returns the new status.

        cloudflare spawns cloudflared and reads its output for the URL in a daemon thread;
        manual just records there's nothing to run (the URL is configured separately).
        """
        self.stop()
        with self._lock:
            self._provider, self._url, self._detail = provider, "", ""
        if provider == "off":
            self._detail = "remote access disabled"
            return self.status()
        if provider == "manual":
            self._detail = "using your own reverse proxy / VPN"
            return self.status()
        if provider == "cloudflare":
            if not cloudflared_available() and spawn is None:
                self._detail = "cloudflared is not installed (brew install cloudflared)"
                return self.status()
            spawn = spawn or _default_spawn
            try:
                proc = spawn(build_cloudflared_cmd(port, token))
            except Exception as exc:
                self._detail = f"could not start cloudflared: {exc}"
                return self.status()
            with self._lock:
                self._proc = proc
                self._detail = "starting tunnel..."
            threading.Thread(target=self._read_url, args=(proc,), daemon=True).start()
            return self.status()
        self._detail = f"unknown provider {provider!r}"
        return self.status()

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            self._url = ""
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _read_url(self, proc) -> None:
        """Read cloudflared's stderr until the public URL appears (or the process ends)."""
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        for raw in iter(stream.readline, ""):
            line = raw.decode() if isinstance(raw, bytes) else raw
            if not line:
                break
            url = parse_public_url(line)
            if url:
                with self._lock:
                    if self._proc is proc:  # not superseded by a newer start/stop
                        self._url = url
                        self._detail = "tunnel up"
                return


def _default_spawn(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


# Process-wide singleton (the tunnel is a property of this running instance).
manager = TunnelManager()
