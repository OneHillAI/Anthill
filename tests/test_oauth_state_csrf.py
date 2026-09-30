"""OAuth login carries an anti-CSRF ``state`` nonce, and the callback rejects a forged one.

Security review: the Google/Microsoft flows sent no ``state`` and the callbacks verified none, so an
attacker could feed a victim a crafted ``/auth/google?code=...`` and log them into the ATTACKER's
account (OAuth login CSRF). The render handlers now set a short-lived ``oauth_state`` cookie, the
authorize links carry the same nonce, and the callback rejects any mismatch before touching the network.
"""

from typing import ClassVar

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app), app_mod


class _DeniedIdentityHTTP:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        class _R:
            status_code = 200

            def json(self):
                return {"access_token": "provider-token"}

        return _R()

    async def get(self, *a, **k):
        class _R:
            status_code = 200

            def json(self):
                return {"email": "stranger@example.com", "name": "Stranger", "id": "sub-1"}

        return _R()


class _FakeHTTP:
    """Records whether the OAuth token exchange was reached (proves the state gate passed)."""

    posted: ClassVar[list[str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        _FakeHTTP.posted.append(url)

        class _R:
            status_code = 400  # force an early, network-free bail after the state check

        return _R()

    async def get(self, *a, **k):
        class _R:
            status_code = 400

            def json(self):
                return {}

        return _R()


def test_setup_page_sets_state_cookie_and_embeds_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    c, _ = _client(tmp_path)  # no org yet -> /setup renders
    r = c.get("/setup")
    assert r.status_code == 200
    assert "oauth_state" in r.cookies  # the anti-CSRF nonce was dropped as a cookie
    assert "state=" in r.text  # ...and the authorize link carries it


def test_callback_rejects_missing_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")  # configured, so the flow reaches the state gate
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda *a, **k: _FakeHTTP())
    _FakeHTTP.posted = []
    r = c.get("/auth/google?code=abc", follow_redirects=False)
    assert r.status_code == 302 and "error=oauth_failed" in r.headers["location"]
    assert _FakeHTTP.posted == []  # rejected before any token exchange


def test_callback_rejects_mismatched_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda *a, **k: _FakeHTTP())
    _FakeHTTP.posted = []
    c.cookies.set("oauth_state", "the-real-nonce")
    r = c.get("/auth/google?code=abc&state=forged", follow_redirects=False)
    assert r.status_code == 302 and "error=oauth_failed" in r.headers["location"]
    assert _FakeHTTP.posted == []  # a forged state never reaches the token exchange


def test_denied_callback_keeps_existing_browser_session(tmp_path, monkeypatch):
    from anthill.web.crypto import hash_password
    from anthill.web.db import Organization, User

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    client, app_mod = _client(tmp_path)
    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    session.add(
        User(
            org_id=org.id,
            email="member@example.com",
            role="member",
            active=True,
            hashed_password=hash_password("member-password-12"),
        )
    )
    session.commit()
    session.close()
    assert (
        client.post(
            "/login",
            data={"email": "member@example.com", "password": "member-password-12"},
            follow_redirects=False,
        ).status_code
        == 302
    )
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda *a, **k: _DeniedIdentityHTTP())
    client.cookies.set("oauth_state", "match-me")

    denied = client.get("/auth/google?code=abc&state=match-me", follow_redirects=False)

    assert denied.status_code == 302
    assert "error=not_invited" in denied.headers["location"]
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_callback_accepts_matching_state_and_proceeds(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda *a, **k: _FakeHTTP())
    _FakeHTTP.posted = []
    c.cookies.set("oauth_state", "match-me")
    c.get("/auth/google?code=abc&state=match-me", follow_redirects=False)
    # A matching state clears the gate, so the callback reaches the (stubbed) token exchange.
    assert _FakeHTTP.posted and "oauth2.googleapis.com/token" in _FakeHTTP.posted[0]
