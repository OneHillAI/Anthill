"""In-app notifications (#284): the notify() chokepoint, the bell unread badge, and the centre page
that marks them read on open. Model-free."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.flush()
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, org.id, user.id


def test_notify_persists_an_unread_notification(tmp_path, monkeypatch):
    _, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    try:
        from anthill.web.notify import notify

        s = app_mod._SessionFactory()
        n = notify(
            s,
            user_id=uid,
            org_id=org_id,
            kind="approval",
            title="Scout needs your approval",
            body="wants to send an email",
            link="/agents/1",
            push=False,
        )
        assert n is not None and n.read is False and n.kind == "approval"
        assert s.query(db_mod.Notification).filter_by(user_id=uid).count() == 1
        s.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_bell_badge_shows_then_centre_marks_read(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    try:
        from anthill.web.notify import notify

        s = app_mod._SessionFactory()
        notify(
            s,
            user_id=uid,
            org_id=org_id,
            kind="approval",
            title="Scout needs your approval",
            body="wants to send an email",
            link="/agents/1",
            push=False,
        )
        s.close()
        # the bell nav item + its unread badge render on any page
        home = client.get("/").text
        assert 'href="/notifications"' in home
        assert 'class="nav-badge"' in home  # the unread count (1) shows on the bell
        # opening the centre lists it, then marks the unread ones read (badge clears next render)
        centre = client.get("/notifications").text
        assert "Scout needs your approval" in centre
        s = app_mod._SessionFactory()
        assert s.query(db_mod.Notification).filter_by(user_id=uid, read=False).count() == 0
        s.close()
        # once read, the badge is gone
        assert 'class="nav-badge"' not in client.get("/").text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
