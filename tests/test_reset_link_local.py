"""Self-service password reset on a solo/local install with no SMTP.

A desktop/solo user has no mail server and never sees the server console, so the reset link is
surfaced directly in the browser - but ONLY for a single-user / solo install. A multi-user org
stays email-only, otherwise the page would leak which emails are registered and hand anyone a
reset link for another user's account.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web.crypto import hash_password
from anthill.web.db import Organization, OrgSettings, User

PW = "correct horse battery"  # >= 12 chars


def _app(tmp_path, monkeypatch, *, users, topology="org"):
    """Build a TestClient with `users` accounts (list of emails) and a deployment topology."""
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)  # no SMTP -> link can't be emailed
    monkeypatch.delenv("ANTHILL_SMTP_FROM", raising=False)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology=topology))
    for email in users:
        s.add(
            User(
                org_id=org.id,
                email=email,
                role="admin",
                active=True,
                hashed_password=hash_password(PW),
            )
        )
    s.commit()
    return TestClient(app_mod.app)


def test_single_user_install_shows_link_in_browser(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, users=["solo@acme.com"], topology="org")
    r = client.post("/forgot", data={"email": "solo@acme.com"})
    assert r.status_code == 200
    assert "Choose a new password" in r.text  # the in-browser CTA
    assert "/reset/" in r.text  # the actual reset link is present


def test_solo_topology_shows_link_even_with_two_users(tmp_path, monkeypatch):
    # Solo deployment is explicitly single-operator: surface the link even if a second row exists.
    client = _app(tmp_path, monkeypatch, users=["a@acme.com", "b@acme.com"], topology="solo")
    r = client.post("/forgot", data={"email": "a@acme.com"})
    assert "Choose a new password" in r.text and "/reset/" in r.text


def test_multiuser_org_directs_to_admin_without_leaking(tmp_path, monkeypatch):
    # A multi-user org never shows the link; it directs to an admin. The message is identical for
    # a matched and an unmatched email, so it can't be used to enumerate registered accounts.
    client = _app(tmp_path, monkeypatch, users=["x@acme.com", "y@acme.com"], topology="org")
    matched = client.post("/forgot", data={"email": "x@acme.com"})
    unmatched = client.post("/forgot", data={"email": "ghost@acme.com"})
    for r in (matched, unmatched):
        assert r.status_code == 200
        assert "Choose a new password" not in r.text and "/reset/" not in r.text
        assert "Ask your administrator" in r.text
    # Identical body either way (enumeration-safe).
    assert matched.text == unmatched.text


def test_unknown_email_shows_no_link(tmp_path, monkeypatch):
    # Even on a single-user install, an unmatched email gets the neutral message (no link).
    client = _app(tmp_path, monkeypatch, users=["solo@acme.com"], topology="org")
    r = client.post("/forgot", data={"email": "nobody@acme.com"})
    assert "Choose a new password" not in r.text and "/reset/" not in r.text
    assert "on its way" in r.text
