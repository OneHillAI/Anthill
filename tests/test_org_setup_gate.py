"""Converting a Solo account to an organisation is a one-way door (no route back), so the
Organisation tab in Settings stays locked behind an explicit confirm+name step instead of being
immediately usable (founder ask, 2026-09-28)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch, *, topology="solo", role="admin"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="Personal", slug="personal")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email=f"{role}@a.com", role=role, active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology=topology)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod


def test_solo_organisation_tab_is_locked_behind_a_name_and_confirm(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, topology="solo")
    body = c.get("/personalize").text
    assert "Set up an organisation" in body
    assert "There is no way back to Solo once you do this." in body
    # locked: the org-admin actions from the converted state are not reachable yet
    assert "Manage people" not in body
    assert "Set up cloud &amp; model" not in body


def test_org_deployment_shows_the_normal_organisation_tab(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, topology="org")
    body = c.get("/personalize").text
    assert "Manage people" in body
    assert "Set up an organisation" not in body


def test_converting_to_org_requires_a_name(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, topology="solo")
    r = c.post("/personalize/organization/setup", data={"org_name": ""}, follow_redirects=False)
    assert "org_error=name_required" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.deployment_topology == "solo"  # unchanged


def test_converting_to_org_sets_topology_and_name(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, topology="solo")
    r = c.post(
        "/personalize/organization/setup",
        data={"org_name": "Acme Corp"},
        follow_redirects=False,
    )
    assert "org_created=1" in r.headers["location"]
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    cfg = s.query(OrgSettings).first()
    assert cfg.deployment_topology == "org"
    assert org.name == "Acme Corp"
    assert org.slug == "acme-corp"
    # the tab now shows the normal, unlocked organisation UI
    body = c.get("/personalize").text
    assert "Manage people" in body


def test_converting_to_org_is_admin_only(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, topology="solo", role="member")
    c.post(
        "/personalize/organization/setup", data={"org_name": "Acme Corp"}, follow_redirects=False
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.deployment_topology == "solo"  # a member's post is a no-op


def test_converting_an_already_converted_org_is_a_no_op(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, topology="org")
    c.post(
        "/personalize/organization/setup",
        data={"org_name": "Second Name"},
        follow_redirects=False,
    )
    org = app_mod._SessionFactory().query(Organization).first()
    assert org.name == "Personal"  # not renamed - already converted, this route is a no-op
