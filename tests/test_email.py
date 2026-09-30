"""Transactional email: templates, multipart delivery, and the flows that trigger sends."""

from typing import ClassVar

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
import anthill.web.mailer as mailer
from anthill.web import email_templates as tpl
from anthill.web.crypto import hash_password
from anthill.web.db import Organization, User

# ── templates ──────────────────────────────────────────────────────────────


def test_templates_render_subject_html_text():
    s, h, t = tpl.welcome(name="Jane", org_name="Acme", url="https://host/")
    assert s == "Welcome to Acme"
    assert "Jane" in h and "Jane" in t
    assert "https://host/" in h and "https://host/" in t

    s, h, t = tpl.password_reset(reset_url="https://host/reset/abc", org_name="Acme")
    assert "reset" in s.lower()
    assert "https://host/reset/abc" in h and "https://host/reset/abc" in t

    s, h, t = tpl.password_changed(org_name="Acme")
    assert "changed" in s.lower()
    assert "did not change" in t.lower()  # the security warning must be present


# ── mailer delivery ─────────────────────────────────────────────────────────


class _FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout=10):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_send_html_is_multipart_and_uses_env(monkeypatch):
    _FakeSMTP.sent.clear()
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    ok = mailer.send_welcome_email("u@x.com", name="J", org_name="Acme", url="https://h/")
    assert ok is True
    msg = _FakeSMTP.sent[-1]
    assert msg["To"] == "u@x.com" and msg["From"] == "noreply@example.com"
    types = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in types and "text/html" in types


def test_send_returns_false_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
    monkeypatch.delenv("ANTHILL_SMTP_FROM", raising=False)
    assert mailer.send_welcome_email("u@x.com") is False
    assert mailer.send_password_changed_email("u@x.com") is False


# ── wiring: the flows call the senders ───────────────────────────────────────


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app)


def test_setup_sends_welcome(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(mailer, "send_welcome_email", lambda to, **k: calls.append(to) or True)
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "admin@x.com", "admin_password": "longenoughpw12", "topology": "solo"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert calls == ["admin@x.com"]


def test_invite_accept_sends_welcome(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(mailer, "send_welcome_email", lambda to, **k: calls.append(to) or True)
    client = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(
        User(org_id=org.id, email="new@x.com", active=False, invite_token="tok123", role="member")
    )
    s.commit()
    r = client.post(
        "/invite/tok123",
        data={"password": "longenoughpw12", "display_name": "New"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert calls == ["new@x.com"]


def test_reset_sends_password_changed(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mailer, "send_password_changed_email", lambda to, **k: calls.append(to) or True
    )
    client = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(
        org_id=org.id,
        email="u@x.com",
        hashed_password=hash_password("oldpassword12"),
        active=True,
        role="member",
    )
    s.add(u)
    s.flush()
    token = app_mod._issue_reset(s, u)
    s.commit()
    r = client.post(f"/reset/{token}", data={"password": "brandnewpw123"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert calls == ["u@x.com"]
