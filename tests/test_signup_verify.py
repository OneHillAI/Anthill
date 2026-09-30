"""Email verification for the self-signup admin (/setup).

The founder who creates the org at /setup must confirm they own the email before the admin account
activates - but only when the org runs a backend AND an email server is configured. A solo/local
install, an org with no SMTP yet, or a send that fails must auto-activate so the founder is never
locked out of the org they just created. Invited members keep verifying via /invite (they set a
password there), and OAuth is provider-verified; neither is affected.
"""

from typing import ClassVar

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


class _Verify:
    """Records send_verify_email calls; toggle .result to simulate a send failure."""

    calls: ClassVar[list] = []
    result: ClassVar[bool] = True

    @classmethod
    def install(cls, monkeypatch, *, result=True):
        cls.calls = []
        cls.result = result
        monkeypatch.setattr(
            mailer,
            "send_verify_email",
            lambda to, url, **k: cls.calls.append((to, url)) or cls.result,
        )
        monkeypatch.setattr(mailer, "send_welcome_email", lambda to, **k: True)


def _user(email="admin@x.com"):
    return app_mod._SessionFactory().query(User).filter(User.email == email).first()


def test_org_with_smtp_requires_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "admin@x.com", "admin_password": "longenoughpw12", "topology": "org"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # the "confirm your email" page, NOT an auto-login redirect
    assert "Confirm your email" in r.text
    assert "session_token" not in r.cookies  # not signed in yet
    assert len(_Verify.calls) == 1 and _Verify.calls[0][0] == "admin@x.com"
    u = _user()
    assert u.active is False and u.invite_token  # pending, holds the confirmation token


def test_pending_signup_keeps_existing_browser_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    signed_in = client.post(
        "/setup",
        data={
            "admin_email": "first@x.com",
            "admin_password": "first-password-12",
            "topology": "solo",
        },
        follow_redirects=False,
    )

    pending = client.post(
        "/setup",
        data={
            "admin_email": "second@x.com",
            "admin_password": "second-password-12",
            "topology": "org",
        },
        follow_redirects=False,
    )

    assert signed_in.status_code == 302
    assert pending.status_code == 200
    assert "session_token" not in pending.cookies
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_org_without_smtp_auto_activates(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
    monkeypatch.delenv("ANTHILL_SMTP_FROM", raising=False)
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "admin@x.com", "admin_password": "longenoughpw12", "topology": "org"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)  # signed straight in - no email to deliver, no lockout
    assert _Verify.calls == []
    assert _user().active is True


def test_solo_auto_activates_even_with_smtp(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "admin@x.com", "admin_password": "longenoughpw12", "topology": "solo"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert _Verify.calls == []  # solo is single-user + local: nothing to verify
    assert _user().active is True


def test_send_failure_falls_back_to_active(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    _Verify.install(monkeypatch, result=False)  # SMTP configured but the send fails
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "admin@x.com", "admin_password": "longenoughpw12", "topology": "org"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)  # do NOT brick the founder out of their own fresh install
    assert len(_Verify.calls) == 1
    u = _user()
    assert u.active is True and u.invite_token is None


def test_verify_link_activates_and_signs_in(tmp_path, monkeypatch):
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(
        User(
            org_id=org.id,
            email="admin@x.com",
            hashed_password=hash_password("longenoughpw12"),
            active=False,
            invite_token="vtok",
            role="admin",
        )
    )
    s.commit()
    r = client.get("/verify/vtok", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.cookies.get("session_token")
    u = _user()
    assert u.active is True and u.invite_token is None


def test_rejected_verification_keeps_existing_browser_session(tmp_path, monkeypatch):
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    assert (
        client.post(
            "/setup",
            data={
                "admin_email": "owner@x.com",
                "admin_password": "owner-password-12",
                "topology": "solo",
            },
            follow_redirects=False,
        ).status_code
        == 302
    )
    session = app_mod._SessionFactory()
    owner = session.query(User).filter(User.email == "owner@x.com").one()
    pending = User(
        org_id=owner.org_id,
        email="pending@x.com",
        hashed_password=hash_password("pending-password-12"),
        active=False,
        invite_token="rejected-token",
        role="member",
    )
    session.add(pending)
    session.commit()
    session.close()

    def reject(db, user, token, **values):
        db.rollback()
        return False

    monkeypatch.setattr(app_mod, "_activate_invited_user", reject)
    response = client.get("/verify/rejected-token", follow_redirects=False)

    assert response.status_code == 200
    assert "invalid" in response.text.lower()
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_verify_invited_member_is_redirected_to_invite(tmp_path, monkeypatch):
    """A passwordless invited member hitting /verify must be sent to /invite (to set a password),
    never half-activated without one."""
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="m@x.com", active=False, invite_token="mtok", role="member"))
    s.commit()
    r = client.get("/verify/mtok", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/invite/mtok"
    assert _user("m@x.com").active is False  # untouched


def test_verify_bad_token_shows_invalid(tmp_path, monkeypatch):
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    r = client.get("/verify/nope", follow_redirects=False)
    assert r.status_code == 200
    assert "invalid" in r.text.lower()


def test_deactivation_cannot_be_reversed_by_verification_resend(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token

    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    admin = User(org_id=org.id, email="owner@x.com", active=True, role="admin")
    pending = User(
        org_id=org.id,
        email="admin@x.com",
        hashed_password=hash_password("longenoughpw12"),
        active=False,
        invite_token="vtok",
        role="admin",
    )
    session.add_all([admin, pending])
    session.commit()
    admin_id, pending_id, org_id = int(admin.id), int(pending.id), int(org.id)
    session.close()
    client.cookies.set("session_token", make_token(admin_id, org_id, "admin"))

    assert client.post(f"/users/{pending_id}/deactivate", follow_redirects=False).status_code == 302
    response = client.post("/verify/resend", data={"email": "admin@x.com"}, follow_redirects=False)

    assert response.status_code == 200
    assert _Verify.calls == []
    user = _user()
    assert user.active is False and user.invite_token is None
    assert client.get("/verify/vtok", follow_redirects=False).status_code == 200


def test_resend_reissues_and_sends(tmp_path, monkeypatch):
    _Verify.install(monkeypatch)
    client = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(
        User(
            org_id=org.id,
            email="admin@x.com",
            hashed_password=hash_password("longenoughpw12"),
            active=False,
            invite_token="old",
            role="admin",
        )
    )
    s.commit()
    r = client.post("/verify/resend", data={"email": "admin@x.com"}, follow_redirects=False)
    assert r.status_code == 200 and "Confirm your email" in r.text
    assert len(_Verify.calls) == 1
    assert _user().invite_token and _user().invite_token != "old"  # a fresh token was issued
