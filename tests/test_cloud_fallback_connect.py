"""Cloud-fallback provider connect: save an encrypted provider key from Settings, flow it into
the HybridPolicy, and validate it via /settings/cloud/test. The provider validation is stubbed so
the route is tested without a real network call."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _admin_client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_save_cloud_key_encrypts_it(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    r = c.post(
        "/settings",
        data={
            "cloud_enabled": "true",
            "cloud_provider": "openrouter",
            "cloud_api_key": "sk-secret-1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_api_key_enc and "sk-secret-1" not in cfg.cloud_api_key_enc  # encrypted at rest


def test_blank_key_keeps_the_saved_one(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )
    saved = app_mod._SessionFactory().query(OrgSettings).first().cloud_api_key_enc
    # resubmit with a blank key -> the saved (encrypted) key is preserved, not wiped
    c.post("/settings", data={"cloud_enabled": "true", "cloud_api_key": ""}, follow_redirects=False)
    assert app_mod._SessionFactory().query(OrgSettings).first().cloud_api_key_enc == saved


def test_stored_key_flows_into_hybrid_policy(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    policy = app_mod._policy_from_cfg(cfg)
    assert (
        policy is not None and policy.api_key == "sk-secret-1"
    )  # decrypted, env no longer required


def test_cloud_test_route_sets_status(tmp_path, monkeypatch):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )

    import anthill.hybrid.providers as providers

    monkeypatch.setattr(providers, "validate_provider", lambda *a, **k: (True, "Authenticated."))
    r = c.post("/settings/cloud/test", follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"]  # JSON now (toast feedback), not a redirect
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_status == "validated" and cfg.cloud_status_detail

    monkeypatch.setattr(providers, "validate_provider", lambda *a, **k: (False, "rejected the key"))
    c.post("/settings/cloud/test", follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_status == "error" and "rejected" in cfg.cloud_status_detail


def test_cloud_test_gives_a_fixed_message_when_the_network_fails(tmp_path, monkeypatch, caplog):
    """docs/specs/bounded-text-and-failure-messages-followup.md: the error text goes to the log only."""
    import logging

    import httpx

    import anthill.hybrid.providers as providers

    marker = "SECRET-MARKER-123"
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )

    def boom(*a, **k):
        raise httpx.ReadTimeout(f"timed out reading {marker}")

    monkeypatch.setattr(providers.httpx, "get", boom)
    with caplog.at_level(logging.WARNING):
        r = c.post("/settings/cloud/test", follow_redirects=False)
    body = r.json()
    assert set(body) == {"ok", "detail", "status"} and body["ok"] is False
    assert marker not in r.text and "Network error reaching" in body["detail"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_status == "error" and marker not in cfg.cloud_status_detail
    assert "ReadTimeout" in caplog.text
    assert marker not in caplog.text and "sk-secret-1" not in caplog.text


class _SilentServer:
    """A local listener that accepts connections and never answers, so a request gets as far as writing its
    headers without any outside network."""

    def __enter__(self):
        import socket
        import threading

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.conns = []

        def accept():
            while True:
                try:
                    self.conns.append(self.sock.accept()[0])
                except OSError:
                    return

        threading.Thread(target=accept, daemon=True).start()
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def __exit__(self, *exc):
        for conn in self.conns:
            conn.close()
        self.sock.close()


@pytest.mark.parametrize(
    "key",
    ["sk-SECRET-KEY-789\n", "sk-SECRET-KEY-789\r\nX-Other: y", "sk-SECRET\x00KEY-789"],
    ids=["newline", "header-break", "nul"],
)
def test_a_key_with_a_header_breaking_character_is_never_logged(key, caplog):
    """httpx quotes the whole Authorization header when a key holds a character a header cannot carry; the
    env fallback key is not stripped, so a trailing newline is enough. Only the class may be logged."""
    import logging

    from anthill.hybrid.providers import CloudProvider, validate_provider

    with _SilentServer() as base_url:
        provider = CloudProvider("local", base_url, "m", "LOCAL_TEST_KEY")
        with caplog.at_level(logging.WARNING):
            ok, detail = validate_provider(provider, key, timeout=5)
    assert ok is False and "Network error reaching local" in detail
    assert "LocalProtocolError" in caplog.text
    assert "SECRET" not in caplog.text and "SECRET" not in detail


def test_a_key_with_a_non_ascii_character_gets_a_fixed_message(caplog):
    """A non-ASCII key cannot be put into a header at all; the error quotes the character. It used to
    escape validate_provider (which promises never to raise)."""
    import logging

    from anthill.hybrid.providers import CloudProvider, validate_provider

    with _SilentServer() as base_url:
        provider = CloudProvider("local", base_url, "m", "LOCAL_TEST_KEY")
        with caplog.at_level(logging.WARNING):
            ok, detail = validate_provider(provider, "sk-SECRET-\u00e9", timeout=5)
    assert ok is False and "has a character that cannot be sent" in detail
    assert "\u00e9" not in detail and "\\xe9" not in detail
    assert "UnicodeEncodeError" in caplog.text
    assert "\u00e9" not in caplog.text and "\\xe9" not in caplog.text
