"""Rolling sessions remain immediately revocable.

Each JWT carries a device-session ID and password generation. Authentication rechecks the live user and
server-side session on every request, so logout, password rotation, deactivation, deletion, and role
changes take effect without waiting for token expiry.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import crypto, db
from anthill.web.crypto import make_token
from anthill.web.db import BrowserSessionOrder, Organization, User


def _setup(tmp_path, *, active=True, role="member"):
    import anthill.web.app as app_mod

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role=role, active=active)
    s.add(u)
    s.commit()
    return app_mod, u.id, o.id


def _set(app_mod, uid, **fields):
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == uid).update(fields)
    s.commit()
    s.close()


def _response_cookie(response, name):
    for header in response.headers.get_list("set-cookie"):
        cookies = SimpleCookie()
        cookies.load(header)
        if name in cookies:
            return cookies[name]
    raise AssertionError(f"response did not set {name}")


def _expiring_token(uid, org_id, *, legacy):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uid),
        "org": org_id,
        "role": "member",
        "exp": now + timedelta(hours=1),
    }
    if not legacy:
        claims.update(iat=now - timedelta(hours=2), sid="aged-session", ver=0)
    return jwt.encode(claims, crypto._JWT_SECRET, algorithm=crypto._ALGORITHM)


def test_active_user_token_authenticates(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    assert app_mod._current_user(session_token=make_token(uid, org_id, "member")) is not None


@pytest.mark.parametrize("legacy", [False, True], ids=["aged", "legacy"])
def test_activity_renews_session_for_thirty_days(tmp_path, legacy):
    app_mod, uid, org_id = _setup(tmp_path)
    original = _expiring_token(uid, org_id, legacy=legacy)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", original, domain="testserver.local", path="/")

    response = c.get("/teams")

    renewed = c.cookies.get("session_renewal")
    assert response.status_code == 200
    assert c.cookies.get("session_token") == original
    assert renewed != original
    assert crypto.decode_token(renewed)["exp"] > time.time() + 29 * 24 * 60 * 60
    assert _response_cookie(response, "session_renewal")["max-age"] == "2592000"


def test_legacy_renewal_preserves_and_refreshes_device_lineage(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    bootstrap = TestClient(app_mod.app)
    bootstrap.cookies.set("session_token", _expiring_token(uid, org_id, legacy=False))
    assert bootstrap.get("/teams").status_code == 200
    device_id = bootstrap.cookies.get("session_device")
    assert device_id

    active = TestClient(app_mod.app)
    active.cookies.set("session_device", device_id)
    active.cookies.set("session_token", _expiring_token(uid, org_id, legacy=False))
    renewed = active.get("/teams")
    refreshed_device = _response_cookie(renewed, "session_device")
    active.cookies.delete("session_token")
    active.cookies.delete("session_device")
    continued = active.get("/teams")

    assert refreshed_device.value == device_id
    assert refreshed_device["max-age"] == "2592000"
    assert continued.status_code == 200
    assert active.cookies.get("session_device") == device_id


def test_chat_stream_upgrades_legacy_session(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    legacy = _expiring_token(uid, org_id, legacy=True)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", legacy, domain="testserver.local", path="/")

    response = c.get("/chat/999/stream", follow_redirects=False)

    assert response.status_code == 404
    assert c.cookies.get("session_token") == legacy
    assert c.cookies.get("session_renewal") != legacy


def test_fresh_session_is_not_reissued_on_every_request(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    c = TestClient(app_mod.app)
    c.cookies.set("session_device", "test-device-0001")
    c.cookies.set("session_token", make_token(uid, org_id, "member"))

    response = c.get("/teams")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_fresh_same_session_renewal_suppresses_redundant_rotation(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    original = _expiring_token(uid, org_id, legacy=False)
    client = TestClient(app_mod.app)
    client.cookies.set("session_device", "test-device-0001")
    client.cookies.set("session_token", original, domain="testserver.local", path="/")

    first = client.get("/teams")
    renewal = client.cookies.get("session_renewal")
    second = client.get("/teams")

    assert first.status_code == 200 and renewal
    assert second.status_code == 200
    assert "set-cookie" not in second.headers
    assert client.cookies.get("session_renewal") == renewal


def test_fresh_authentication_retires_presented_sessions(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    stale = [make_token(uid, org_id, "member") for _ in range(2)]
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", stale[0], domain="testserver.local", path="/")
    client.cookies.set("session_renewal", stale[1], domain="testserver.local", path="/")
    assert client.get("/teams").status_code == 200

    response = client.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert client.cookies.get("session_token") not in stale
    assert client.cookies.get("session_renewal") is None
    for token in stale:
        replay = TestClient(app_mod.app)
        replay.cookies.set("session_token", token, domain="testserver.local", path="/")
        assert replay.get("/teams", follow_redirects=False).status_code == 303


def test_logged_out_token_cannot_be_replayed(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    original = _expiring_token(uid, org_id, legacy=False)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", original, domain="testserver.local", path="/")
    assert c.get("/teams").status_code == 200
    renewed = c.cookies.get("session_renewal")
    assert renewed != original

    assert c.get("/logout", follow_redirects=False).status_code == 302
    for replay in (original, renewed):
        c.cookies.set("session_token", replay, domain="testserver.local", path="/")
        response = c.get("/teams", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert "u@a.com" not in c.get("/docs/how-it-works").text


def test_logout_revokes_every_presented_session(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    session = app_mod._SessionFactory()
    other = User(org_id=org_id, email="other@a.com", role="member", active=True)
    session.add(other)
    session.commit()
    other_id = int(other.id)
    session.close()
    tokens = (
        make_token(uid, org_id, "member"),
        make_token(other_id, org_id, "member"),
    )
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", tokens[0], domain="testserver.local", path="/")
    client.cookies.set("session_renewal", tokens[1], domain="testserver.local", path="/")

    assert client.get("/logout", follow_redirects=False).status_code == 302

    for token in tokens:
        replay = TestClient(app_mod.app)
        replay.cookies.set("session_token", token, domain="testserver.local", path="/")
        assert replay.get("/teams", follow_redirects=False).status_code == 303


def test_server_high_water_rejects_lower_order_after_cookies_are_gone(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    client = TestClient(app_mod.app)
    login = client.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )
    current = crypto.decode_token(login.cookies["session_token"])

    assert client.get("/logout", follow_redirects=False).status_code == 302

    lower_order_token = make_token(
        uid,
        org_id,
        "member",
        session_order=int(current["ord"]),
        device_id=str(current["dev"]),
    )
    replay = TestClient(app_mod.app)
    replay.cookies.set("session_token", lower_order_token)
    response = replay.get("/teams", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unsigned_order_suffix_cannot_revive_stale_session(tmp_path):
    app_mod, uid, _ = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    original = TestClient(app_mod.app)
    first = original.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )
    stale = first.cookies["session_token"]
    stale_claims = crypto.decode_token(stale)
    assert original.get("/teams").status_code == 200

    current = TestClient(app_mod.app)
    current.cookies.set("session_device", stale_claims["dev"])
    replacement = current.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )
    current_claims = crypto.decode_token(replacement.cookies["session_token"])

    replay = TestClient(app_mod.app)
    replay.cookies.set("session_device", stale_claims["dev"])
    replay.cookies.set("session_token", stale)
    replay.cookies.set(f"session_order_{current_claims['ord']}", stale)
    rejected = replay.post(
        "/account/password",
        data={"current_password": "wrong", "new_password": "another-long-password"},
        follow_redirects=False,
    )

    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/login"
    assert "session_token" not in rejected.cookies
    assert current.get("/account").status_code == 200


def test_conflicting_device_cookie_cannot_authenticate(tmp_path):
    app_mod, uid, _org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    signed_in = TestClient(app_mod.app)
    login = signed_in.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )

    conflicting = TestClient(app_mod.app)
    conflicting.cookies.update(signed_in.cookies)
    conflicting.cookies.delete("session_device")
    conflicting.cookies.set("session_device", "different-device-0001")
    response = conflicting.get("/account", follow_redirects=False)

    assert login.status_code == 302
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_revoked_session_cannot_logout_current_session(tmp_path):
    app_mod, uid, _org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    current = TestClient(app_mod.app)
    first = current.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )
    stale = first.cookies["session_token"]
    stale_claims = crypto.decode_token(stale)
    assert current.get("/teams").status_code == 200
    assert (
        current.post(
            "/login",
            data={"email": "u@a.com", "password": "long-enough-password"},
            follow_redirects=False,
        ).status_code
        == 302
    )

    replay = TestClient(app_mod.app)
    replay.cookies.set("session_device", stale_claims["dev"])
    replay.cookies.set("session_token", stale)
    replay.cookies.set(f"session_order_{stale_claims['ord']}", stale)
    assert replay.get("/logout", follow_redirects=False).status_code == 302

    assert current.get("/account").status_code == 200


def test_authentication_advances_durable_order_beyond_process_clock(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    device_id = "durable-device-0001"
    high_water = 9_000_000_000_000_000_000
    session = app_mod._SessionFactory()
    session.add(BrowserSessionOrder(id=device_id, high_water=high_water))
    session.commit()
    session.close()
    current = make_token(
        uid,
        org_id,
        "member",
        session_order=high_water,
        device_id=device_id,
    )
    client = TestClient(app_mod.app)
    client.cookies.set("session_device", device_id)
    client.cookies.set("session_token", current)
    client.cookies.set(f"session_order_{high_water}", current)

    response = client.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )

    replacement = response.cookies.get("session_token")
    assert response.status_code == 302
    assert replacement is not None
    assert int(crypto.decode_token(replacement)["ord"]) == high_water + 1
    assert client.get("/teams").status_code == 200


def test_invalid_authentication_requests_do_not_write_order_state(tmp_path, monkeypatch):
    app_mod, uid, _org_id = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "configured")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "configured")
    client = TestClient(app_mod.app)

    assert (
        client.post(
            "/login",
            data={"email": "u@a.com", "password": "wrong-password"},
        ).status_code
        == 401
    )
    assert client.post("/reset/invalid", data={"password": "valid-new-password"}).status_code == 200
    assert (
        client.post("/invite/invalid", data={"password": "valid-new-password"}).status_code == 404
    )
    assert client.get("/verify/invalid").status_code == 200
    assert client.get("/auth/google?code=invalid&state=invalid").status_code == 200
    assert client.get("/auth/microsoft?code=invalid&state=invalid").status_code == 200
    assert client.get("/logout", follow_redirects=False).status_code == 302

    session = app_mod._SessionFactory()
    assert session.query(BrowserSessionOrder).count() == 0
    session.close()


def test_logout_wins_when_renewal_overlaps(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    app_mod, uid, org_id = _setup(tmp_path)
    original = _expiring_token(uid, org_id, legacy=False)
    activity = TestClient(app_mod.app)
    logout = TestClient(app_mod.app)
    for client in (activity, logout):
        client.cookies.set("session_token", original, domain="testserver.local", path="/")

    renewing = Event()
    release = Event()
    renew = app_mod._renew_session_token

    def delayed_renewal(claims):
        renewing.set()
        assert release.wait(timeout=5)
        return renew(claims)

    monkeypatch.setattr(app_mod, "_renew_session_token", delayed_renewal)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(activity.get, "/teams")
        try:
            assert renewing.wait(timeout=5)
            assert logout.get("/logout", follow_redirects=False).status_code == 302
        finally:
            release.set()
        assert "session_renewal" not in response.result(timeout=5).headers.get("set-cookie", "")

    replay = TestClient(app_mod.app)
    replay.cookies.set("session_token", original, domain="testserver.local", path="/")
    assert replay.get("/teams", follow_redirects=False).status_code == 303


def test_password_change_revokes_other_sessions(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    first = TestClient(app_mod.app)
    second = TestClient(app_mod.app)
    first.cookies.set(
        "session_token", make_token(uid, org_id, "member"), domain="testserver.local", path="/"
    )
    second.cookies.set(
        "session_token", make_token(uid, org_id, "member"), domain="testserver.local", path="/"
    )
    assert first.get("/teams").status_code == 200
    assert second.get("/teams").status_code == 200

    changed = first.post(
        "/account/password",
        data={"new_password": "new-password-123"},
        follow_redirects=False,
    )

    assert changed.status_code == 302
    assert first.get("/teams").status_code == 200
    assert second.get("/teams", follow_redirects=False).status_code == 303


def test_concurrent_password_changes_invalidate_first_replacement(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    app_mod, uid, org_id = _setup(tmp_path)
    contenders = [TestClient(app_mod.app), TestClient(app_mod.app)]
    for contender in contenders:
        contender.cookies.set(
            "session_token",
            make_token(uid, org_id, "member"),
            domain="testserver.local",
            path="/",
        )
        assert contender.get("/teams").status_code == 200

    def change(args):
        i, contender = args
        return contender.post(
            "/account/password",
            data={"new_password": f"concurrent-password-{i}"},
            follow_redirects=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(change, enumerate(contenders)))

    assert sorted(r.status_code for r in responses) == [302, 303]
    replacement_tokens = [r.cookies.get("session_token") for r in responses if r.status_code == 302]
    assert len(replacement_tokens) == 1
    replay = TestClient(app_mod.app)
    replay.cookies.set("session_token", replacement_tokens[0], domain="testserver.local", path="/")
    assert replay.get("/teams", follow_redirects=False).status_code == 200


def test_delayed_renewal_cannot_replace_password_change_cookie(tmp_path):
    from http.cookies import SimpleCookie

    app_mod, uid, org_id = _setup(tmp_path)
    c = TestClient(app_mod.app)
    original = _expiring_token(uid, org_id, legacy=False)
    c.cookies.set("session_token", original, domain="testserver.local", path="/")
    renewal_response = c.get("/teams")
    delayed_set_cookie = renewal_response.headers["set-cookie"]

    changed = c.post(
        "/account/password",
        data={"new_password": "new-password-123"},
        follow_redirects=False,
    )
    fresh = changed.cookies.get("session_token")
    assert changed.status_code == 302 and fresh

    delayed = SimpleCookie()
    delayed.load(delayed_set_cookie)
    morsel = next(iter(delayed.values()))
    c.cookies.set(morsel.key, morsel.value, domain="testserver.local", path="/")

    assert c.get("/teams", follow_redirects=False).status_code == 200
    assert c.cookies.get("session_token") == fresh


def test_valid_primary_session_wins_over_another_users_renewal(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    session = app_mod._SessionFactory()
    other = User(
        org_id=org_id,
        email="other@a.com",
        role="member",
        active=True,
        auth_version=7,
    )
    session.add(other)
    session.commit()
    other_id = other.id
    session.close()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "member"))
    client.cookies.set(
        "session_renewal",
        make_token(other_id, org_id, "member", auth_version=7),
    )

    response = client.get("/account")

    assert response.status_code == 200
    assert "u@a.com" in response.text
    assert "other@a.com" not in response.text


def test_non_default_session_hours_controls_session_lifetime():
    script = """
import json
import time
from starlette.responses import Response
from anthill.web import crypto
from anthill.web.app import _set_session_cookie

claims = crypto.decode_token(crypto.make_token(1, 1, "member"))
response = Response()
_set_session_cookie(response, "token", secure=False)
now = time.time()
print(json.dumps({
    "ttl": claims["exp"] - claims["iat"],
    "cookie": response.headers["set-cookie"],
    "before_threshold": crypto.session_needs_renewal({**claims, "iat": now - 29 * 60}),
    "after_threshold": crypto.session_needs_renewal({**claims, "iat": now - 31 * 60}),
}))
"""
    env = os.environ.copy()
    env["ANTHILL_SESSION_HOURS"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    behavior = json.loads(result.stdout)

    assert behavior["ttl"] == 60 * 60
    assert "Max-Age=3600" in behavior["cookie"]
    assert behavior["before_threshold"] is False
    assert behavior["after_threshold"] is True


@pytest.mark.parametrize(
    ("base_url", "secure"),
    [("https://testserver", True), ("http://localhost", False)],
)
def test_fresh_login_cookie_security_matches_transport(tmp_path, base_url, secure):
    app_mod, uid, _ = _setup(tmp_path)
    _set(app_mod, uid, hashed_password=app_mod.hash_password("long-enough-password"))
    client = TestClient(app_mod.app, base_url=base_url)

    response = client.post(
        "/login",
        data={"email": "u@a.com", "password": "long-enough-password"},
        follow_redirects=False,
    )

    session_cookie = next(
        cookie for cookie in response.cookies.jar if cookie.name == "session_token"
    )
    assert response.status_code == 302
    assert session_cookie.secure is secure


def test_https_renewal_cookie_is_secure(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    old = _expiring_token(uid, org_id, legacy=False)
    c = TestClient(app_mod.app, base_url="https://testserver")
    c.cookies.set("session_token", old, domain="testserver.local", path="/")

    response = c.get("/teams")

    assert response.status_code == 200
    assert _response_cookie(response, "session_renewal")["secure"]


def test_forced_password_reset_stops_existing_session(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(uid, org_id, "member"))
    assert c.get("/teams").status_code == 200

    _set(app_mod, uid, must_reset_password=True)

    response = c.get("/teams", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_deactivated_user_token_stops_authenticating(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    token = make_token(uid, org_id, "member")
    assert app_mod._current_user(session_token=token) is not None  # was valid...
    _set(app_mod, uid, active=False)
    assert (
        app_mod._current_user(session_token=token) is None
    )  # ...revoked the instant it deactivates


def test_deleted_user_token_stops_authenticating(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    token = make_token(uid, org_id, "member")
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == uid).delete()
    s.commit()
    s.close()
    assert app_mod._current_user(session_token=token) is None


def test_role_comes_from_db_not_the_token(tmp_path):
    # A token that CLAIMS admin for a member user must not grant admin - the DB role wins.
    app_mod, uid, org_id = _setup(tmp_path, role="member")
    forged = make_token(uid, org_id, "admin")
    claims = app_mod._current_user(session_token=forged)
    assert claims is not None and claims["role"] == "member"


def test_demotion_takes_effect_immediately(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path, role="admin")
    token = make_token(uid, org_id, "admin")
    assert app_mod._current_user(session_token=token)["role"] == "admin"
    _set(app_mod, uid, role="member")
    assert app_mod._current_user(session_token=token)["role"] == "member"


def test_deactivated_user_is_redirected_from_a_protected_page(tmp_path):
    app_mod, uid, org_id = _setup(tmp_path)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(uid, org_id, "member"))
    assert c.get("/teams").status_code == 200  # a logged-in user reaches it
    _set(app_mod, uid, active=False)
    r = c.get("/teams", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"  # now bounced to login
