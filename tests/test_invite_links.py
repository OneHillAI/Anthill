"""In-UI invite links: an admin can copy a pending member's invite link from the Users page,
instead of digging it out of the server console. The link is rendered in the (admin-only) page
body, never in a URL parameter.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings, User

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
    pending = User(
        org_id=org.id,
        email="invitee@acme.com",
        role="member",
        active=False,
        invite_token="tok-abc123",
    )
    # The org is activated (a validated backend) so invites are allowed (see the activation gate).
    cfg = OrgSettings(
        org_id=org.id, org_backend_status="validated", org_model_endpoint="https://e/v1"
    )
    s.add_all([admin, pending, cfg])
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id}


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_pending_invite_shows_copyable_link(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/users")
    assert r.status_code == 200
    assert 'onclick="copyInvite' in r.text  # the copy button is rendered for the pending member
    assert "invite/tok-abc123" in r.text  # ...wired to the real link (in the body, not a URL param)


def test_active_member_has_no_invite_link(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    # activate the invitee + clear its token (what accepting the invite does)
    s = app_mod._SessionFactory()
    from anthill.web.db import User

    inv = s.query(User).filter(User.email == "invitee@acme.com").first()
    inv.active = True
    inv.invite_token = None
    s.commit()
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/users")
    assert r.status_code == 200
    # No copy button + no token in the body once active (the static helper text still mentions it).
    assert 'onclick="copyInvite' not in r.text
    assert "invite/tok-abc123" not in r.text


def test_deactivation_wins_over_in_flight_invite_activation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    admin, app_mod, ids = _app(tmp_path, monkeypatch)
    invitee = TestClient(app_mod.app)
    _auth(admin, ids["admin"], ids["org"])
    hashing = Event()
    resume = Event()
    real_hash = app_mod.hash_password

    def pause_after_invite_lookup(password):
        hashing.set()
        resume.wait(5)
        return real_hash(password)

    monkeypatch.setattr(app_mod, "hash_password", pause_after_invite_lookup)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            invitee.post,
            "/invite/tok-abc123",
            data={"password": "new-member-password"},
            follow_redirects=False,
        )
        assert hashing.wait(5)
        try:
            session = app_mod._SessionFactory()
            member_id = (
                session.query(db_mod.User.id)
                .filter(db_mod.User.email == "invitee@acme.com")
                .scalar()
            )
            session.close()
            deactivated = admin.post(f"/users/{member_id}/deactivate", follow_redirects=False)
        finally:
            resume.set()
        activation = pending.result()

    session = app_mod._SessionFactory()
    member = session.query(db_mod.User).filter(db_mod.User.id == member_id).one()
    assert deactivated.status_code == 302
    assert activation.status_code == 404
    assert "session_token" not in activation.cookies
    assert member.active is False
    assert member.invite_token is None
    assert member.hashed_password is None
    session.close()


def test_rejected_invite_keeps_existing_browser_session(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    client, app_mod, _ids = _app(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    admin = session.query(db_mod.User).filter(db_mod.User.email == "admin@acme.com").one()
    admin.hashed_password = app_mod.hash_password("current-admin-password")
    session.commit()
    session.close()
    assert (
        client.post(
            "/login",
            data={"email": "admin@acme.com", "password": "current-admin-password"},
            follow_redirects=False,
        ).status_code
        == 302
    )
    hashing = Event()
    resume = Event()
    real_hash = app_mod.hash_password

    def pause_after_lookup(password):
        hashing.set()
        assert resume.wait(5)
        return real_hash(password)

    monkeypatch.setattr(app_mod, "hash_password", pause_after_lookup)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post,
            "/invite/tok-abc123",
            data={"password": "new-member-password"},
            follow_redirects=False,
        )
        assert hashing.wait(5)
        try:
            session = app_mod._SessionFactory()
            session.query(db_mod.User).filter(db_mod.User.email == "invitee@acme.com").update(
                {db_mod.User.active: False, db_mod.User.invite_token: None}
            )
            session.commit()
            session.close()
        finally:
            resume.set()
        rejected = pending.result()

    assert rejected.status_code == 404
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_deactivation_forces_in_flight_activation_back_to_inactive(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    admin, app_mod, ids = _app(tmp_path, monkeypatch)
    invitee = TestClient(app_mod.app)
    _auth(admin, ids["admin"], ids["org"])
    session = app_mod._SessionFactory()
    member_id = (
        session.query(db_mod.User.id).filter(db_mod.User.email == "invitee@acme.com").scalar()
    )
    session.close()
    loaded = Event()
    resume = Event()

    def pause_after_load(_session, instance):
        if not loaded.is_set() and isinstance(instance, db_mod.User) and instance.id == member_id:
            loaded.set()
            assert resume.wait(5)

    event.listen(Session, "loaded_as_persistent", pause_after_load)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                admin.post,
                f"/users/{member_id}/deactivate",
                follow_redirects=False,
            )
            assert loaded.wait(5)
            try:
                activation = invitee.post(
                    "/invite/tok-abc123",
                    data={"password": "new-member-password"},
                    follow_redirects=False,
                )
            finally:
                resume.set()
            deactivated = pending.result()
    finally:
        resume.set()
        event.remove(Session, "loaded_as_persistent", pause_after_load)

    session = app_mod._SessionFactory()
    member = session.query(db_mod.User).filter(db_mod.User.id == member_id).one()
    assert activation.status_code == 302
    assert deactivated.status_code == 302
    assert member.active is False
    assert member.invite_token is None
    assert invitee.get("/teams", follow_redirects=False).status_code == 303
    session.close()


def test_invite_then_link_is_available(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/users/invite",
        data={"email": "new@acme.com", "role": "member"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "invited=new@acme.com" in r.headers["location"]
    page = client.get("/users").text
    # Both pending members (the seeded one + the just-invited one) get a copy button.
    assert "new@acme.com" in page and page.count('onclick="copyInvite') >= 2
