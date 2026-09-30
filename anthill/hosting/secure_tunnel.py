"""Shared SSH-tunnel helper for bare-VM LLM provisioners (Option A of
docs/specs/llm-endpoint-secure-transport.md).

Closes the inference port rather than merely encrypting it: vLLM binds to loopback on the VM, and an
outbound ``ssh -N -L`` local port-forward keyed by a per-member Ed25519 key is the only reachable path
in. Mirrors the supervised-subprocess shape of ``anthill/remote/tunnel.py``'s ``TunnelManager`` (Popen +
``poll()``-based liveness, injectable spawn), but keyed per ``(org, member)`` rather than a single global
tunnel, and carrying private-key material rather than none - a council can have more than one Lambda-backed
member, each needing its own live tunnel.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

SpawnFn = Callable[[list[str]], subprocess.Popen]


@dataclass(frozen=True)
class TunnelKeypair:
    private_key_openssh: str
    public_key_line: str


def generate_tunnel_keypair() -> TunnelKeypair:
    """A fresh Ed25519 keypair in OpenSSH wire format, pure-Python (no ``ssh-keygen`` subprocess), so
    generation is unit-testable and every provision() call gets a new key (the rotate-on-reprovision
    posture the spec asks for falls out of this for free)."""
    private = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    return TunnelKeypair(
        private_key_openssh=private_bytes.decode("ascii"),
        public_key_line=public_bytes.decode("ascii"),
    )


def restricted_authorized_keys_line(public_key_line: str, *, remote_port: int = 8000) -> str:
    """The public key line, prefixed with an ``authorized_keys`` option string that limits it to a
    port-forward only: no shell (a forced no-op command), no pty/agent/X11 forwarding, and ``permitopen``
    scoped to the loopback inference port - so a leak of this key opens no shell and reaches nothing but
    the VM's own vLLM port, per the spec's Security Considerations."""
    options = (
        'command="echo no-shell",'
        "no-agent-forwarding,"
        "no-X11-forwarding,"
        "no-pty,"
        f'permitopen="127.0.0.1:{remote_port}"'
    )
    return f"{options} {public_key_line.strip()}"


def free_local_port() -> int:
    """A free ephemeral loopback port from the OS. Chosen once at first successful tunnel establishment
    and persisted from there - every later restart (including on Anthill reboot) reuses the stored port,
    since ``org_model_endpoint`` embeds it and must not change under callers that cached the URL."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _default_spawn(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class SSHTunnelManager:
    """Process-wide supervisor of keyed ``ssh -N -L`` local forwards - one per ``"{org_id}:{member_index}"``.

    Each tunnel's private key is written to a 0600 temp file for the life of the process (``ssh -i`` needs
    a path, not a string) and removed on ``stop()``. ``start()`` is idempotent: calling it again for a key
    that already has a live process replaces it (drops the old handle first), which is what both a normal
    re-provision and the boot-time autostart need.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._keyfiles: dict[str, str] = {}

    def start(
        self,
        key: str,
        *,
        host: str,
        user: str,
        remote_port: int,
        local_port: int,
        private_key_openssh: str,
        spawn: SpawnFn | None = None,
    ) -> None:
        spawn = spawn or _default_spawn
        with self._lock:
            self._stop_locked(key)
            fd, path = tempfile.mkstemp(prefix="anthill-tunnel-", suffix=".key")
            try:
                os.write(fd, private_key_openssh.encode("ascii"))
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
            argv = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ExitOnForwardFailure=yes",
                "-i",
                path,
                "-N",
                "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
                f"{user}@{host}",
            ]
            self._procs[key] = spawn(argv)
            self._keyfiles[key] = path

    def stop(self, key: str) -> None:
        with self._lock:
            self._stop_locked(key)

    def _stop_locked(self, key: str) -> None:
        proc = self._procs.pop(key, None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        path = self._keyfiles.pop(key, None)
        if path is not None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def status(self, key: str) -> dict:
        with self._lock:
            proc = self._procs.get(key)
            return {"running": proc is not None and proc.poll() is None}


# Process-wide singleton, mirroring anthill/remote/tunnel.py's `manager`.
manager = SSHTunnelManager()
