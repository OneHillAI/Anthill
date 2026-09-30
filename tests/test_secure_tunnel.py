"""Shared SSH-tunnel helper for bare-VM LLM provisioners (Option A,
docs/specs/llm-endpoint-secure-transport.md): keypair generation, the restricted authorized_keys line,
and the supervised local-forward manager. No real ``ssh`` subprocess or network I/O - ``SSHTunnelManager``
tests always inject a fake ``spawn``, mirroring ``anthill/remote/tunnel.py``'s existing test convention of
a fresh manager instance per test rather than the process-wide singleton.
"""

import os
import socket

from cryptography.hazmat.primitives import serialization

from anthill.hosting import secure_tunnel

# ── keypair generation ──────────────────────────────────────────────────────────────


def test_generate_tunnel_keypair_round_trips_through_the_openssh_loader():
    kp = secure_tunnel.generate_tunnel_keypair()
    assert kp.public_key_line.startswith("ssh-ed25519 ")
    # The public key line loads back through cryptography's own OpenSSH parser unchanged - proves the
    # format is well-formed without needing a live SSH server.
    loaded = serialization.load_ssh_public_key(kp.public_key_line.encode("ascii"))
    reencoded = loaded.public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    )
    assert reencoded.decode("ascii") == kp.public_key_line
    # The private key also loads back (no password, matching NoEncryption() at generation).
    serialization.load_ssh_private_key(kp.private_key_openssh.encode("ascii"), password=None)


def test_generate_tunnel_keypair_is_fresh_every_call():
    a = secure_tunnel.generate_tunnel_keypair()
    b = secure_tunnel.generate_tunnel_keypair()
    assert (
        a.public_key_line != b.public_key_line
    )  # rotate-on-reprovision falls out of this for free


# ── restricted authorized_keys line ─────────────────────────────────────────────────


def test_restricted_authorized_keys_line_is_port_forward_only():
    kp = secure_tunnel.generate_tunnel_keypair()
    line = secure_tunnel.restricted_authorized_keys_line(kp.public_key_line, remote_port=8000)
    assert 'permitopen="127.0.0.1:8000"' in line
    assert "no-pty" in line
    assert "no-agent-forwarding" in line
    assert "no-X11-forwarding" in line
    assert 'command="' in line  # a forced no-op command - no shell access
    assert line.endswith(kp.public_key_line)


# ── free_local_port ─────────────────────────────────────────────────────────────────


def test_free_local_port_returns_a_bindable_loopback_port():
    port = secure_tunnel.free_local_port()
    assert 0 < port < 65536
    # Must actually be free right after - bind to it to prove it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


# ── SSHTunnelManager ─────────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, *, dies_immediately=False):
        self._returncode = 0 if dies_immediately else None
        self.terminated = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0


def test_start_reports_running_and_writes_a_restricted_keyfile():
    tm = secure_tunnel.SSHTunnelManager()
    captured = {}

    def spawn(argv):
        captured["argv"] = argv
        return _FakeProc()

    tm.start(
        "org1:0",
        host="1.2.3.4",
        user="ubuntu",
        remote_port=8000,
        local_port=54321,
        private_key_openssh="fake-key-material",
        spawn=spawn,
    )
    assert tm.status("org1:0")["running"] is True
    keyfile = tm._keyfiles["org1:0"]
    assert os.path.exists(keyfile)
    assert oct(os.stat(keyfile).st_mode)[-3:] == "600"
    with open(keyfile) as f:
        assert f.read() == "fake-key-material"
    argv = captured["argv"]
    assert "-N" in argv and "-L" in argv
    assert "127.0.0.1:54321:127.0.0.1:8000" in argv
    assert "ubuntu@1.2.3.4" in argv
    assert "-i" in argv and keyfile in argv


def test_stop_terminates_and_removes_the_keyfile():
    tm = secure_tunnel.SSHTunnelManager()
    proc = _FakeProc()
    tm.start(
        "org1:0",
        host="h",
        user="u",
        remote_port=8000,
        local_port=1,
        private_key_openssh="k",
        spawn=lambda argv: proc,
    )
    keyfile = tm._keyfiles["org1:0"]
    tm.stop("org1:0")
    assert proc.terminated is True
    assert tm.status("org1:0")["running"] is False
    assert not os.path.exists(keyfile)


def test_stop_on_an_unknown_key_is_a_safe_no_op():
    tm = secure_tunnel.SSHTunnelManager()
    tm.stop("never-started")  # must not raise


def test_status_reflects_a_dead_process_without_an_explicit_stop():
    tm = secure_tunnel.SSHTunnelManager()
    tm.start(
        "org1:0",
        host="h",
        user="u",
        remote_port=8000,
        local_port=1,
        private_key_openssh="k",
        spawn=lambda argv: _FakeProc(dies_immediately=True),
    )
    assert tm.status("org1:0")["running"] is False


def test_start_is_idempotent_and_replaces_a_prior_live_handle():
    tm = secure_tunnel.SSHTunnelManager()
    first = _FakeProc()
    tm.start(
        "org1:0",
        host="h",
        user="u",
        remote_port=8000,
        local_port=1,
        private_key_openssh="k",
        spawn=lambda argv: first,
    )
    first_keyfile = tm._keyfiles["org1:0"]
    second = _FakeProc()
    tm.start(
        "org1:0",
        host="h",
        user="u",
        remote_port=8000,
        local_port=2,
        private_key_openssh="k2",
        spawn=lambda argv: second,
    )
    assert first.terminated is True  # the old handle was stopped, not leaked
    assert not os.path.exists(first_keyfile)
    assert tm.status("org1:0")["running"] is True
