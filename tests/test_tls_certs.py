"""TLS CA-bundle discovery for the frozen app (desktop._ensure_tls_certs).

The packaged app bundles its own OpenSSL, whose compiled-in default cert paths do not exist on the
user's machine, so outbound TLS (SMTP email, HTTPS APIs) fails cert verification and the mailer
swallows it - which once looked exactly like "email isn't configured". _ensure_tls_certs points
OpenSSL at a real bundle (certifi, else the OS bundle) without ever overriding a working default or
an operator-pinned value, and without raising.
"""

from __future__ import annotations

import os

from anthill import desktop


def test_pins_certifi_when_frozen(monkeypatch) -> None:
    """A frozen build with no SSL_CERT_FILE gets pointed at a real, existing CA bundle."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    desktop._ensure_tls_certs()
    pinned = os.environ.get("SSL_CERT_FILE")
    assert pinned and os.path.exists(pinned)


def test_respects_operator_pinned_value(monkeypatch) -> None:
    """An operator-set SSL_CERT_FILE is never overridden."""
    monkeypatch.setenv("SSL_CERT_FILE", "/my/custom/ca.pem")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    desktop._ensure_tls_certs()
    assert os.environ["SSL_CERT_FILE"] == "/my/custom/ca.pem"


def test_noop_on_healthy_from_source(monkeypatch) -> None:
    """A normal from-source interpreter whose default trust store resolves is left untouched."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(desktop.sys, "frozen", False, raising=False)
    import ssl

    paths = ssl.get_default_verify_paths()
    default_ok = bool(paths.cafile and os.path.exists(paths.cafile)) or bool(
        paths.capath and os.path.isdir(paths.capath)
    )
    desktop._ensure_tls_certs()
    if default_ok:
        # healthy machine: we must not have pinned anything
        assert os.environ.get("SSL_CERT_FILE") is None
    else:
        # unusual machine with no system trust store: we still fix it
        assert os.environ.get("SSL_CERT_FILE")


def test_never_raises_without_certifi(monkeypatch) -> None:
    """Cert discovery must never block startup, even if certifi can't be imported."""
    import builtins

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError("simulated: no certifi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    # Must complete (falling back to the OS bundle or doing nothing) rather than raise.
    assert desktop._ensure_tls_certs() is None
