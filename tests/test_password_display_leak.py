"""Security: a user's password must never be stored as (or leak through) their display name.

A browser/password-manager can misfill the name field with the password on the invite/setup forms; the
server drops a name equal to the password, and a startup remediation scrubs any that already leaked.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import hash_password


def _app(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return app_mod


def test_invite_never_stores_the_password_as_display_name(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "SuperSecret123!"
    s.add(
        User(
            org_id=org.id,
            email="new@acme.com",
            role="member",
            active=False,
            invite_token="tok123",
            display_name="",
        )
    )
    s.commit()
    s.close()
    try:
        client = TestClient(app_mod.app)
        # the browser misfilled the name field with the password
        client.post(
            "/invite/tok123", data={"password": pw, "display_name": pw}, follow_redirects=False
        )
        s2 = app_mod._SessionFactory()
        got = s2.query(User).filter_by(email="new@acme.com").first()
        assert got.display_name == "new@acme.com" and got.display_name != pw
        s2.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_startup_scrubs_an_existing_leaked_password_display_name(tmp_path, monkeypatch):
    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "LeakedPassword99"
    # an existing user whose display_name IS their cleartext password (the leak)
    s.add(
        User(
            org_id=org.id,
            email="leak@acme.com",
            role="member",
            active=True,
            display_name=pw,
            hashed_password=hash_password(pw),
        )
    )
    # a normal user whose (long) display name is NOT their password - must be left alone
    s.add(
        User(
            org_id=org.id,
            email="jane@acme.com",
            role="member",
            active=True,
            display_name="Jane Alexandra Smith",
            hashed_password=hash_password("a-different-password"),
        )
    )
    s.commit()
    s.close()
    try:
        app_mod._scrub_password_display_names(app_mod._engine)
        s2 = app_mod._SessionFactory()
        leaked = s2.query(User).filter_by(email="leak@acme.com").first()
        normal = s2.query(User).filter_by(email="jane@acme.com").first()
        assert leaked.display_name == "leak@acme.com" and leaked.display_name != pw  # scrubbed
        assert normal.display_name == "Jane Alexandra Smith"  # untouched
        s2.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_account_name_refuses_the_password_as_display_name(tmp_path, monkeypatch):
    # The display-name edit form (/account/name) is on the same page as the password fields, so a
    # password manager can misfill it. The handler bcrypt-checks the submitted name and refuses it if it
    # is the password, falling back to the email.
    from fastapi.testclient import TestClient

    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "MyRealPassword12"
    u = User(
        org_id=org.id,
        email="u@acme.com",
        role="member",
        active=True,
        display_name="Jane",
        hashed_password=hash_password(pw),
    )
    s.add(u)
    s.commit()
    uid, org_id = u.id, org.id
    s.close()
    try:
        client = TestClient(app_mod.app)
        client.cookies.set("session_token", make_token(uid, org_id, "member"))
        client.post("/account/name", data={"display_name": pw}, follow_redirects=False)
        s2 = app_mod._SessionFactory()
        got = s2.query(User).filter_by(id=uid).first()
        assert got.display_name != pw and got.display_name == "u@acme.com"  # refused -> email
        s2.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_remediation_forces_a_reset_not_just_a_scrub(tmp_path, monkeypatch):
    # The core (#591 follow-up): an exposed password is COMPROMISED, so scrubbing the copy is not enough -
    # the account is flagged to force a password reset, and its owner is notified.
    from anthill.web.db import Notification, Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "LeakedPassword99"
    u = User(
        org_id=org.id,
        email="leak@acme.com",
        role="member",
        active=True,
        display_name=pw,
        hashed_password=hash_password(pw),
    )
    s.add(u)
    s.commit()
    uid = u.id
    s.close()
    try:
        app_mod._scrub_password_display_names(app_mod._engine)
        s2 = app_mod._SessionFactory()
        u2 = s2.query(User).filter_by(email="leak@acme.com").first()
        assert u2.display_name == "leak@acme.com"  # copy scrubbed
        assert u2.must_reset_password is True  # AND flagged compromised -> forced reset
        assert s2.query(Notification).filter_by(user_id=uid).count() >= 1  # user was told
        s2.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_login_on_a_compromised_password_forces_reset_and_issues_no_session(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "ExposedPassword12"
    s.add(
        User(
            org_id=org.id,
            email="x@acme.com",
            role="member",
            active=True,
            display_name="x@acme.com",
            hashed_password=hash_password(pw),
            must_reset_password=True,
        )
    )
    s.commit()
    s.close()
    try:
        c = TestClient(app_mod.app)
        r = c.post("/login", data={"email": "x@acme.com", "password": pw}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/reset/")  # forced to reset, not "/"
        assert "session_token" not in r.cookies  # NO session on the exposed password
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_normal_login_still_works(tmp_path, monkeypatch):
    # regression guard: an ordinary (unflagged) account logs in normally and gets a session.
    from fastapi.testclient import TestClient

    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    pw = "NormalPassword123"
    s.add(
        User(
            org_id=org.id,
            email="ok@acme.com",
            role="admin",
            active=True,
            display_name="Okay Person",
            hashed_password=hash_password(pw),
        )
    )
    s.commit()
    s.close()
    try:
        c = TestClient(app_mod.app)
        r = c.post("/login", data={"email": "ok@acme.com", "password": pw}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert r.cookies.get("session_token")  # a real session
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_reset_clears_the_forced_flag(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anthill.web.app import _issue_reset
    from anthill.web.db import Organization, User

    app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(
        org_id=org.id,
        email="r@acme.com",
        role="member",
        active=True,
        display_name="r@acme.com",
        hashed_password=hash_password("OldExposed12345"),
        must_reset_password=True,
    )
    s.add(u)
    s.flush()
    tok = _issue_reset(s, u)
    s.commit()
    s.close()
    try:
        c = TestClient(app_mod.app)
        c.post(f"/reset/{tok}", data={"password": "BrandNewPassword1"}, follow_redirects=False)
        s2 = app_mod._SessionFactory()
        got = s2.query(User).filter_by(email="r@acme.com").first()
        assert got.must_reset_password is False  # rotated -> flag cleared, no more forced reset
        s2.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
