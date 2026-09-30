"""Self-service password reset + account recovery.

Covers the four pieces: forgot-password (enumeration-safe, time-limited token), reset-password
(single-use, logs in), change-password in account settings, and the admin "send reset" action.
Mirrors the TestClient harness in test_invite_links.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import hash_password, make_token, verify_password

KNOWN_PW = "correct horse battery"  # 21 chars, >= the 12-char minimum


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)  # force the console/copy-link path
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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    member = User(
        org_id=org.id,
        email="member@acme.com",
        role="member",
        active=True,
        hashed_password=hash_password(KNOWN_PW),
    )
    s.add_all([admin, member])
    s.commit()
    ids = {"org": org.id, "admin": admin.id, "member": member.id}
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, uid, org_id, role="admin"):
    client.cookies.set("session_token", make_token(uid, org_id, role))


def _get_user(app_mod, email):
    from anthill.web.db import User

    return app_mod._SessionFactory().query(User).filter(User.email == email).first()


# ── forgot ────────────────────────────────────────────────────────────────────


def test_forgot_issues_token_for_active_user(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    r = client.post("/forgot", data={"email": "member@acme.com"})
    # Multi-user org, no SMTP: the page directs to an admin (same message matched-or-not).
    assert r.status_code == 200 and "Ask your administrator" in r.text
    u = _get_user(app_mod, "member@acme.com")
    assert u.reset_token and u.reset_expires  # a time-limited token was persisted


def test_forgot_unknown_email_is_enumeration_safe(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    r = client.post("/forgot", data={"email": "nobody@acme.com"})
    # Identical response to the matched case (both show the contact-admin message), no token minted.
    assert r.status_code == 200 and "Ask your administrator" in r.text
    assert _get_user(app_mod, "nobody@acme.com") is None


# ── reset ───────────────────────────────────────────────────────────────────────


def test_reset_sets_password_and_is_single_use(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    old_session = TestClient(app_mod.app)
    _auth(old_session, ids["member"], ids["org"], role="member")
    assert old_session.get("/teams").status_code == 200
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token

    form = client.get(f"/reset/{token}")
    assert form.status_code == 200 and 'name="password"' in form.text

    done = client.post(
        f"/reset/{token}", data={"password": "a-brand-new-passphrase"}, follow_redirects=False
    )
    assert done.status_code == 302 and done.headers["location"] == "/"
    assert "session_token" in done.cookies  # logged straight in

    u = _get_user(app_mod, "member@acme.com")
    assert verify_password("a-brand-new-passphrase", u.hashed_password)
    assert not verify_password(KNOWN_PW, u.hashed_password)  # old password no longer works
    assert u.reset_token is None and u.reset_expires is None  # token consumed
    assert old_session.get("/teams", follow_redirects=False).status_code == 303

    # The same link can't be replayed.
    assert "expired" in client.get(f"/reset/{token}").text.lower()


def test_old_password_login_racing_reset_cannot_authenticate(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    login_client = TestClient(app_mod.app)
    reset_client = TestClient(app_mod.app)
    verified = Event()
    resume = Event()
    real_verify = app_mod.verify_password

    def pause_after_verification(plain, hashed):
        result = real_verify(plain, hashed)
        if plain == KNOWN_PW and result:
            verified.set()
            resume.wait(5)
        return result

    monkeypatch.setattr(app_mod, "verify_password", pause_after_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_login = pool.submit(
            login_client.post,
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert verified.wait(5)
        try:
            reset = reset_client.post(
                f"/reset/{token}",
                data={"password": "reset-wins-passphrase"},
                follow_redirects=False,
            )
        finally:
            resume.set()
        login = pending_login.result()

    assert reset.status_code == 302
    assert login.status_code == 401
    assert "session_token" not in login.cookies
    assert login_client.get("/teams", follow_redirects=False).status_code == 303
    assert verify_password(
        "reset-wins-passphrase", _get_user(app_mod, "member@acme.com").hashed_password
    )


def test_delayed_cross_account_login_cannot_replace_newer_reset_session(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    from anthill.web.db import User

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    db = app_mod._SessionFactory()
    other = User(
        org_id=ids["org"],
        email="other@acme.com",
        role="member",
        active=True,
        hashed_password=hash_password(KNOWN_PW),
    )
    db.add(other)
    db.commit()
    db.close()
    client.post("/forgot", data={"email": "member@acme.com"})
    reset_token = _get_user(app_mod, "member@acme.com").reset_token
    login_client = TestClient(app_mod.app)
    reset_client = TestClient(app_mod.app)
    for contender in (login_client, reset_client):
        contender.cookies.set("session_device", "shared-browser-0001")
    login_committed = Event()
    resume_login = Event()
    revoke_presented_sessions = app_mod._revoke_presented_sessions

    def pause_login_response(request, *names):
        if request.url.path == "/login":
            login_committed.set()
            assert resume_login.wait(5)
        revoke_presented_sessions(request, *names)

    monkeypatch.setattr(app_mod, "_revoke_presented_sessions", pause_login_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_login = pool.submit(
            login_client.post,
            "/login",
            data={"email": "other@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert login_committed.wait(5)
        try:
            reset = reset_client.post(
                f"/reset/{reset_token}",
                data={"password": "reset-wins-passphrase"},
                follow_redirects=False,
            )
        finally:
            resume_login.set()
        login = pending_login.result()

    browser = TestClient(app_mod.app)
    browser.cookies.update(reset.cookies)
    browser.cookies.update(login.cookies)
    account = browser.get("/account")
    assert reset.status_code == 302
    assert login.status_code == 302
    assert account.status_code == 200
    assert "member@acme.com" in account.text
    assert "other@acme.com" not in account.text


def test_legacy_session_logout_wins_over_delayed_login(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    _client, app_mod, ids = _app(tmp_path, monkeypatch)
    delayed_login = TestClient(app_mod.app)
    logout_client = TestClient(app_mod.app)
    legacy = make_token(ids["member"], ids["org"], "member")
    for contender in (delayed_login, logout_client):
        contender.cookies.set("session_token", legacy)
    verified = Event()
    resume = Event()
    verify = app_mod.verify_password

    def pause_after_verification(plain, hashed):
        result = verify(plain, hashed)
        if plain == KNOWN_PW and result:
            verified.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(app_mod, "verify_password", pause_after_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_login = pool.submit(
            delayed_login.post,
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert verified.wait(5)
        try:
            logout = logout_client.get("/logout", follow_redirects=False)
        finally:
            resume.set()
        login = pending_login.result()

    assert logout.status_code == 302
    assert login.status_code == 302
    assert "session_token" not in login.cookies
    assert delayed_login.get("/account", follow_redirects=False).status_code == 303


def test_missing_device_cookie_logout_wins_over_delayed_login(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    _client, app_mod, _ids = _app(tmp_path, monkeypatch)
    signed_in = TestClient(app_mod.app)
    assert (
        signed_in.post(
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        ).status_code
        == 302
    )
    delayed_login = TestClient(app_mod.app)
    logout_client = TestClient(app_mod.app)
    for contender in (delayed_login, logout_client):
        contender.cookies.update(signed_in.cookies)
        contender.cookies.delete("session_device")
    verified = Event()
    resume = Event()
    real_verify = app_mod.verify_password

    def pause_after_verification(plain, hashed):
        result = real_verify(plain, hashed)
        if plain == KNOWN_PW and result:
            verified.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(app_mod, "verify_password", pause_after_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_login = pool.submit(
            delayed_login.post,
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert verified.wait(5)
        try:
            logout = logout_client.get("/logout", follow_redirects=False)
        finally:
            resume.set()
        login = pending_login.result()

    assert logout.status_code == 302
    assert login.status_code == 302 and login.headers["location"] == "/login"
    assert "session_token" not in login.cookies
    assert delayed_login.get("/account", follow_redirects=False).status_code == 303


def test_logout_wins_over_delayed_anonymous_login(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from fastapi.testclient import TestClient

    _client, app_mod, _ids = _app(tmp_path, monkeypatch)
    delayed = TestClient(app_mod.app)
    newer = TestClient(app_mod.app)
    for contender in (delayed, newer):
        contender.cookies.set("session_device", "shared-browser-0001")
    verified = Event()
    resume = Event()
    lock = Lock()
    paused = False
    verify = app_mod.verify_password

    def pause_first_verification(plain, hashed):
        nonlocal paused
        result = verify(plain, hashed)
        with lock:
            should_pause = result and not paused
            if should_pause:
                paused = True
        if should_pause:
            verified.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(app_mod, "verify_password", pause_first_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            delayed.post,
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert verified.wait(5)
        try:
            login = newer.post(
                "/login",
                data={"email": "member@acme.com", "password": KNOWN_PW},
                follow_redirects=False,
            )
            logout = newer.get("/logout", follow_redirects=False)
        finally:
            resume.set()
        stale = pending.result()

    assert login.status_code == 302
    assert logout.status_code == 302
    assert stale.status_code == 302 and stale.headers["location"] == "/login"
    assert "session_token" not in stale.cookies
    assert delayed.get("/account", follow_redirects=False).status_code == 303


def test_logout_wins_after_login_commits_before_response(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    _client, app_mod, _ids = _app(tmp_path, monkeypatch)
    signed_in = TestClient(app_mod.app)
    assert (
        signed_in.post(
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        ).status_code
        == 302
    )
    delayed_login = TestClient(app_mod.app)
    logout_client = TestClient(app_mod.app)
    for contender in (delayed_login, logout_client):
        contender.cookies.update(signed_in.cookies)
    committed = Event()
    resume = Event()
    set_session = app_mod._set_fresh_session_cookie

    def pause_before_response(response, request, token, *, secure, order):
        set_session(response, request, token, secure=secure, order=order)
        if request.url.path == "/login":
            committed.set()
            assert resume.wait(5)

    monkeypatch.setattr(app_mod, "_set_fresh_session_cookie", pause_before_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            delayed_login.post,
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        )
        assert committed.wait(5)
        try:
            logout = logout_client.get("/logout", follow_redirects=False)
        finally:
            resume.set()
        delayed = pending.result()

    browser = TestClient(app_mod.app)
    browser.cookies.update(logout.cookies)
    browser.cookies.update(delayed.cookies)
    assert logout.status_code == 302
    assert "session_token" in delayed.cookies
    assert browser.get("/account", follow_redirects=False).status_code == 303


def test_logout_reserves_order_before_password_request_authentication(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from fastapi.testclient import TestClient

    _client, app_mod, _ids = _app(tmp_path, monkeypatch)
    signed_in = TestClient(app_mod.app)
    login = signed_in.post(
        "/login",
        data={"email": "member@acme.com", "password": KNOWN_PW},
        follow_redirects=False,
    )
    assert login.status_code == 302
    changer = TestClient(app_mod.app)
    logout_client = TestClient(app_mod.app)
    changer.cookies.update(signed_in.cookies)
    logout_client.cookies.update(signed_in.cookies)
    authenticating = Event()
    resume = Event()
    lock = Lock()
    paused = False
    revalidate = app_mod._revalidate_user

    def pause_first_authentication(claims, *, accepted_order=None):
        nonlocal paused
        with lock:
            should_pause = not paused
            paused = True
        if should_pause:
            authenticating.set()
            assert resume.wait(5)
        return revalidate(claims, accepted_order=accepted_order)

    monkeypatch.setattr(app_mod, "_revalidate_user", pause_first_authentication)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_change = pool.submit(
            changer.post,
            "/account/password",
            data={"current_password": KNOWN_PW, "new_password": "changed-passphrase"},
            follow_redirects=False,
        )
        assert authenticating.wait(5)
        try:
            logout = logout_client.get("/logout", follow_redirects=False)
        finally:
            resume.set()
        change = pending_change.result()

    assert logout.status_code == 302
    assert change.status_code == 303
    assert change.headers["location"] == "/login"
    assert "session_token" not in change.cookies
    assert verify_password(KNOWN_PW, _get_user(app_mod, "member@acme.com").hashed_password)


def test_logout_wins_while_password_replacement_is_hashing(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    _client, app_mod, _ids = _app(tmp_path, monkeypatch)
    signed_in = TestClient(app_mod.app)
    assert (
        signed_in.post(
            "/login",
            data={"email": "member@acme.com", "password": KNOWN_PW},
            follow_redirects=False,
        ).status_code
        == 302
    )
    change_client = TestClient(app_mod.app)
    logout_client = TestClient(app_mod.app)
    for contender in (change_client, logout_client):
        contender.cookies.update(signed_in.cookies)
    password_hashed = Event()
    resume_change = Event()
    real_hash = app_mod.hash_password

    def pause_after_hash(plain):
        result = real_hash(plain)
        if plain == "changed-passphrase":
            password_hashed.set()
            assert resume_change.wait(5)
        return result

    monkeypatch.setattr(app_mod, "hash_password", pause_after_hash)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_change = pool.submit(
            change_client.post,
            "/account/password",
            data={"current_password": KNOWN_PW, "new_password": "changed-passphrase"},
            follow_redirects=False,
        )
        assert password_hashed.wait(5)
        try:
            logout = logout_client.get("/logout", follow_redirects=False)
        finally:
            resume_change.set()
        change = pending_change.result()

    assert logout.status_code == 302
    assert change.status_code == 303 and change.headers["location"] == "/login"
    assert "session_token" not in change.cookies
    assert verify_password(KNOWN_PW, _get_user(app_mod, "member@acme.com").hashed_password)
    assert change_client.get("/account", follow_redirects=False).status_code == 303


def test_stale_reset_cannot_invalidate_newer_login(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    client, app_mod, _ids = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    reset_client = TestClient(app_mod.app)
    login_client = TestClient(app_mod.app)
    for contender in (reset_client, login_client):
        contender.cookies.set("session_device", "shared-browser-0001")
    password_hashed = Event()
    resume_reset = Event()
    real_hash = app_mod.hash_password

    def pause_after_hash(plain):
        result = real_hash(plain)
        if plain == "stale-reset-passphrase":
            password_hashed.set()
            assert resume_reset.wait(5)
        return result

    monkeypatch.setattr(app_mod, "hash_password", pause_after_hash)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_reset = pool.submit(
            reset_client.post,
            f"/reset/{token}",
            data={"password": "stale-reset-passphrase"},
            follow_redirects=False,
        )
        assert password_hashed.wait(5)
        try:
            login = login_client.post(
                "/login",
                data={"email": "member@acme.com", "password": KNOWN_PW},
                follow_redirects=False,
            )
        finally:
            resume_reset.set()
        reset = pending_reset.result()

    assert login.status_code == 302
    assert reset.status_code == 302 and reset.headers["location"] == "/login"
    assert "session_token" not in reset.cookies
    assert login_client.get("/account", follow_redirects=False).status_code == 200
    assert verify_password(KNOWN_PW, _get_user(app_mod, "member@acme.com").hashed_password)


def test_password_change_racing_reset_cannot_overwrite_it(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    changer = TestClient(app_mod.app)
    reset_client = TestClient(app_mod.app)
    _auth(changer, ids["member"], ids["org"], role="member")
    verified = Event()
    resume = Event()
    real_verify = app_mod.verify_password

    def pause_after_verification(plain, hashed):
        result = real_verify(plain, hashed)
        if plain == KNOWN_PW and result:
            verified.set()
            resume.wait(5)
        return result

    monkeypatch.setattr(app_mod, "verify_password", pause_after_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_change = pool.submit(
            changer.post,
            "/account/password",
            data={
                "current_password": KNOWN_PW,
                "new_password": "stale-change-passphrase",
            },
            follow_redirects=False,
        )
        assert verified.wait(5)
        try:
            reset = reset_client.post(
                f"/reset/{token}",
                data={"password": "reset-wins-passphrase"},
                follow_redirects=False,
            )
        finally:
            resume.set()
        change = pending_change.result()

    stored_hash = _get_user(app_mod, "member@acme.com").hashed_password
    assert reset.status_code == 302
    assert change.status_code == 303 and change.headers["location"] == "/login"
    assert verify_password("reset-wins-passphrase", stored_hash)
    assert not verify_password("stale-change-passphrase", stored_hash)


def test_reset_token_is_atomic_single_use(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    clients = [TestClient(app_mod.app), TestClient(app_mod.app)]

    def reset(args):
        i, contender = args
        return contender.post(
            f"/reset/{token}",
            data={"password": f"concurrent-password-{i}"},
            follow_redirects=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(reset, enumerate(clients)))

    assert sorted(r.status_code for r in responses) == [200, 302]
    assert sum("session_token" in r.cookies for r in responses) == 1


def test_deactivation_invalidates_outstanding_reset(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    _auth(client, ids["admin"], ids["org"])
    assert (
        client.post(f"/users/{ids['member']}/deactivate", follow_redirects=False).status_code == 302
    )

    response = client.post(
        f"/reset/{token}", data={"password": "cannot-reactivate-me"}, follow_redirects=False
    )

    assert response.status_code == 200
    assert "session_token" not in response.cookies
    assert _get_user(app_mod, "member@acme.com").active is False


def test_reset_rejects_expired_token(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    # Expire the token by hand.
    s = app_mod._SessionFactory()
    from anthill.web.db import User

    u = s.query(User).filter(User.id == ids["member"]).first()
    token = u.reset_token
    u.reset_expires = 1  # epoch 1970 — long gone
    s.commit()

    assert "expired" in client.get(f"/reset/{token}").text.lower()
    bad = client.post(f"/reset/{token}", data={"password": "another-passphrase-xyz"})
    assert "expired" in bad.text.lower()
    # Password unchanged.
    assert verify_password(KNOWN_PW, _get_user(app_mod, "member@acme.com").hashed_password)


def test_reset_enforces_minimum_length(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/forgot", data={"email": "member@acme.com"})
    token = _get_user(app_mod, "member@acme.com").reset_token
    r = client.post(f"/reset/{token}", data={"password": "short"}, follow_redirects=False)
    assert r.status_code == 400 and "at least" in r.text
    # Token survives a rejected attempt so the user can retry.
    assert _get_user(app_mod, "member@acme.com").reset_token == token


# ── change password (account) ───────────────────────────────────────────────────


def test_account_requires_login(tmp_path, monkeypatch):
    client, _app_mod, _ = _app(tmp_path, monkeypatch)
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_change_password_happy_path(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    # The Account page renders (extends base.html) and offers the change-password form.
    acct = client.get("/account")
    assert acct.status_code == 200 and "Change password" in acct.text
    r = client.post(
        "/account/password",
        data={"current_password": KNOWN_PW, "new_password": "my-fresh-passphrase"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "changed=1" in r.headers["location"]
    assert verify_password(
        "my-fresh-passphrase", _get_user(app_mod, "member@acme.com").hashed_password
    )


def test_change_password_wrong_current_rejected(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    r = client.post(
        "/account/password",
        data={"current_password": "not-my-password", "new_password": "my-fresh-passphrase"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=bad_current" in r.headers["location"]
    assert "session_token" not in r.cookies
    assert not any(name.startswith("session_order_") for name in r.cookies)
    assert client.get("/account").status_code == 200
    # Unchanged.
    assert verify_password(KNOWN_PW, _get_user(app_mod, "member@acme.com").hashed_password)


def test_short_password_rejection_keeps_session_active(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")

    response = client.post(
        "/account/password",
        data={"current_password": KNOWN_PW, "new_password": "short"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "error=too_short" in response.headers["location"]
    assert "session_token" not in response.cookies
    assert not any(name.startswith("session_order_") for name in response.cookies)
    assert client.get("/account").status_code == 200


# ── admin "send reset" ──────────────────────────────────────────────────────────


def test_admin_reset_issues_link_and_renders_copy_button(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(f"/users/{ids['member']}/reset", follow_redirects=False)
    assert r.status_code == 302 and "reset=member@acme.com" in r.headers["location"]
    token = _get_user(app_mod, "member@acme.com").reset_token
    assert token
    page = client.get("/users").text
    assert 'onclick="copyReset' in page and f"reset/{token}" in page


def test_admin_reset_cross_org_404(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    # A user in a different org must not be resettable.
    from anthill.web.db import Organization, User

    s = app_mod._SessionFactory()
    other = Organization(name="Other", slug="other")
    s.add(other)
    s.flush()
    victim = User(org_id=other.id, email="victim@other.com", role="member", active=True)
    s.add(victim)
    s.commit()
    _auth(client, ids["admin"], ids["org"])
    assert client.post(f"/users/{victim.id}/reset", follow_redirects=False).status_code == 404


def test_admin_reset_requires_admin(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    assert client.post(f"/users/{ids['admin']}/reset", follow_redirects=False).status_code == 403
