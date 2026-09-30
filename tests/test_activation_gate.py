"""Org activation gate (P3): an admin cannot invite a team until the org backend is connected and
validated. A shared organization needs that backend to exist; "org on a laptop" is impossible."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _app(tmp_path, monkeypatch, *, activated: bool):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
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
    cfg = OrgSettings(org_id=org.id)
    if activated:
        cfg.org_backend_status = "validated"
        cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.add_all([admin, cfg])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, app_mod, org.id


def _members(app_mod, org_id):
    return app_mod._SessionFactory().query(User).filter(User.org_id == org_id).count()


def test_invite_blocked_until_activated(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch, activated=False)
    before = _members(app_mod, org_id)
    r = client.post(
        "/users/invite", data={"email": "new@acme.com", "role": "member"}, follow_redirects=False
    )
    assert r.status_code == 302 and "error=not_activated" in r.headers["location"]
    assert _members(app_mod, org_id) == before  # no invitee row created


def test_invite_allowed_once_activated(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch, activated=True)
    r = client.post(
        "/users/invite", data={"email": "new@acme.com", "role": "member"}, follow_redirects=False
    )
    assert r.status_code == 302 and "invited=new@acme.com" in r.headers["location"]
    assert (
        app_mod._SessionFactory().query(User).filter(User.email == "new@acme.com").first()
        is not None
    )


def test_users_page_shows_activation_notice_when_not_activated(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, activated=False)
    page = client.get("/users").text
    assert "Activate your organization to invite a team" in page
    assert "+ Invite user" not in page  # the invite button is hidden until activated
    assert "Set up organization" in page  # ...replaced by a link to the Organization page


def test_users_page_shows_invite_button_when_activated(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, activated=True)
    page = client.get("/users").text
    assert "+ Invite user" in page
    assert "Activate your organization to invite a team" not in page
