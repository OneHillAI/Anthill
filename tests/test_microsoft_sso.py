"""Microsoft (Azure AD) single sign-on: same invited-users-only policy as Google, plus
the login button + callback wiring.
"""

import base64
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.app import _jwt_claims, oauth_login_outcome
from anthill.web.db import Organization, User, create_tables


def _make_jwt(claims: dict) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def test_jwt_claims_reads_payload():
    jwt = _make_jwt({"email": "bob@acme.com", "name": "Bob", "oid": "o-1"})
    assert _jwt_claims(jwt) == {"email": "bob@acme.com", "name": "Bob", "oid": "o-1"}
    assert _jwt_claims("not-a-jwt") == {}


def test_microsoft_invited_user_activated_and_linked(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(
        User(org_id=org.id, email="alice@acme.com", role="member", active=False, invite_token="t1")
    )
    s.commit()
    u, err = oauth_login_outcome(s, "alice@acme.com", "ms-sub-1", "Alice", provider="microsoft")
    assert err == "" and u is not None
    assert u.active is True and u.oauth_provider == "microsoft" and u.invite_token is None


def test_microsoft_unknown_email_rejected(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'o2.db'}")
    create_tables(eng)
    s = sessionmaker(bind=eng)()
    s.add(Organization(name="Acme", slug="acme"))
    s.commit()
    u, err = oauth_login_outcome(s, "stranger@hotmail.com", "sub", "Stranger", provider="microsoft")
    assert u is None and err == "not_invited"


# ── routes ──────────────────────────────────────────────────────────────────────


def _client(tmp_path, monkeypatch, *, with_org=True):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    if with_org:
        s = app_mod._SessionFactory()
        s.add(Organization(name="Acme", slug="acme"))
        s.commit()
    return TestClient(app_mod.app)


def test_login_shows_microsoft_button_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "ms-client-id")
    monkeypatch.setenv("MICROSOFT_TENANT", "common")
    client = _client(tmp_path, monkeypatch)
    r = client.get("/login")
    assert r.status_code == 200
    assert "Continue with Microsoft" in r.text
    assert "login.microsoftonline.com/common/oauth2/v2.0/authorize" in r.text


def test_login_hides_microsoft_button_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    client = _client(tmp_path, monkeypatch)
    assert "Continue with Microsoft" not in client.get("/login").text


def test_microsoft_callback_cancelled_and_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    client = _client(tmp_path, monkeypatch)
    # no code -> cancelled
    r1 = client.get("/auth/microsoft", follow_redirects=False)
    assert r1.status_code == 302 and "oauth_cancelled" in r1.headers["location"]
    # code present but provider not configured -> microsoft_not_configured
    r2 = client.get("/auth/microsoft?code=abc", follow_redirects=False)
    assert r2.status_code == 302 and "microsoft_not_configured" in r2.headers["location"]
