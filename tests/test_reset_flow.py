"""Password reset always emails when an email server is configured - never an in-browser link.

The in-browser reset link exists ONLY as a lifeline for a solo/desktop install with NO email server
(where the operator can't see a server console). Once SMTP is configured, email is the only channel:
always send, never surface a link (a desktop user has no browser to click one), and a failed send is
an honest error, not a fallback to a link. Multi-user orgs never leak (enumeration-safe).
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
import anthill.web.mailer as mailer
from anthill.web.crypto import hash_password
from anthill.web.db import Organization, User


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


def _seed(*, extra_users=0):
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(
        User(
            org_id=org.id,
            email="u@x.com",
            hashed_password=hash_password("oldpassword12"),
            active=True,
            role="admin",
        )
    )
    for i in range(extra_users):
        s.add(User(org_id=org.id, email=f"other{i}@x.com", active=True, role="member"))
    s.commit()


def _smtp(monkeypatch, on: bool):
    if on:
        monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    else:
        monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
        monkeypatch.delenv("ANTHILL_SMTP_FROM", raising=False)


def test_smtp_configured_emails_and_never_shows_link(tmp_path, monkeypatch):
    _smtp(monkeypatch, True)
    calls = []
    monkeypatch.setattr(mailer, "send_reset_email", lambda to, url, **k: calls.append(to) or True)
    client = _client(tmp_path, monkeypatch)
    _seed()
    r = client.post("/forgot", data={"email": "u@x.com"})
    assert r.status_code == 200
    assert calls == ["u@x.com"]  # emailed
    assert "/reset/" not in r.text  # NO in-browser link, ever, when email is configured
    assert "on its way" in r.text.lower()


def test_smtp_send_failure_shows_error_not_link(tmp_path, monkeypatch):
    _smtp(monkeypatch, True)
    monkeypatch.setattr(mailer, "send_reset_email", lambda to, url, **k: False)  # send fails
    client = _client(tmp_path, monkeypatch)
    _seed()  # single user -> solo
    r = client.post("/forgot", data={"email": "u@x.com"})
    assert r.status_code == 200
    assert "/reset/" not in r.text  # still NO link
    assert "couldn't send" in r.text.lower()  # honest error instead


def test_no_smtp_solo_still_offers_link_as_lifeline(tmp_path, monkeypatch):
    _smtp(monkeypatch, False)
    client = _client(tmp_path, monkeypatch)
    _seed()  # single user -> solo, no email server
    r = client.post("/forgot", data={"email": "u@x.com"})
    assert r.status_code == 200
    assert "/reset/" in r.text  # lifeline preserved: no other way to reset


def test_no_smtp_multiuser_contacts_admin_no_leak(tmp_path, monkeypatch):
    _smtp(monkeypatch, False)
    client = _client(tmp_path, monkeypatch)
    _seed(extra_users=2)  # 3 users -> not solo
    r = client.post("/forgot", data={"email": "u@x.com"})
    assert r.status_code == 200
    assert "/reset/" not in r.text  # never leak a link in a multi-user org
    # a matched and an unmatched address must render identically (enumeration-safe)
    r2 = client.post("/forgot", data={"email": "nobody@x.com"})
    assert r.text == r2.text
