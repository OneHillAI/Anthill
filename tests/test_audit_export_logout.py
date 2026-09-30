"""Issue #451 (secondary coverage): session end and data-export events are audited, so the admin
Audit log records sign-outs and every time data leaves the perimeter (training export, file
download, wiki export) - not just sign-ins. The primary org_id=NULL fix is in test_audit_login_visibility."""

from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import AuditLog, Organization, Team, TeamMembership, User


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path / "files"))
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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return c, org.id, admin.id


def _events(event):
    return app_mod._SessionFactory().query(AuditLog).filter(AuditLog.event == event).all()


def _response_cookies(response):
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return cookies


def test_logout_is_audited(tmp_path, monkeypatch):
    c, org_id, uid = _client(tmp_path, monkeypatch)
    r = c.get("/logout", follow_redirects=False)
    assert r.status_code == 302
    cookies = _response_cookies(r)
    assert cookies["session_token"]["max-age"] == "0"
    assert cookies["session_renewal"]["max-age"] == "0"
    assert cookies["session_device"]["max-age"] == "2592000"
    tombstones = [
        cookie
        for name, cookie in cookies.items()
        if name.startswith("session_order_") and cookie.value == "logged-out"
    ]
    assert len(tombstones) == 1
    assert tombstones[0]["max-age"] == "2592000"
    rows = _events("user.logout")
    assert len(rows) == 1 and rows[0].org_id == org_id and rows[0].user_id == uid
    assert rows[0].ip == "testclient"  # request IP is recorded (spec acceptance criterion)


def test_logout_without_session_is_not_audited(tmp_path, monkeypatch):
    c, _org, _uid = _client(tmp_path, monkeypatch)
    c.cookies.clear()  # not signed in -> nothing to attribute, no event
    assert c.get("/logout", follow_redirects=False).status_code == 302
    assert _events("user.logout") == []


def test_file_download_is_audited(tmp_path, monkeypatch):
    from anthill.agent.tools import files_owner

    c, org_id, uid = _client(tmp_path, monkeypatch)
    # files are per-user now (owner = org + user), so write into the requester's own dir
    fdir = tmp_path / "files" / files_owner(org_id, uid)
    fdir.mkdir(parents=True)
    (fdir / "report.txt").write_text("hello")
    r = c.get("/files/report.txt")
    assert r.status_code == 200 and r.text == "hello"
    rows = _events("file.download")
    assert len(rows) == 1 and rows[0].org_id == org_id and "report.txt" in rows[0].detail
    assert rows[0].ip == "testclient"


def test_missing_file_download_is_not_audited(tmp_path, monkeypatch):
    c, _org, _uid = _client(tmp_path, monkeypatch)
    assert c.get("/files/nope.txt").status_code == 404
    assert _events("file.download") == []  # a 404 is not data leaving


def test_wiki_export_is_audited(tmp_path, monkeypatch):
    c, org_id, _uid = _client(tmp_path, monkeypatch)
    r = c.get("/wiki/export.okgf.tgz?scope=org")
    assert r.status_code == 200
    rows = _events("wiki.export")
    assert len(rows) == 1 and rows[0].org_id == org_id
    assert rows[0].detail == "scope=org" and rows[0].ip == "testclient"


def test_team_scoped_wiki_export_records_the_team(tmp_path, monkeypatch):
    """The team-scoped export path records team_id in the detail (a distinct detail format)."""
    c, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    team = Team(org_id=org_id, name="Proj", slug="proj", owner_id=uid)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=uid, role="owner", status="active"))
    s.commit()
    r = c.get(f"/wiki/export.okgf.tgz?scope=team&team_id={team.id}")
    assert r.status_code == 200
    rows = _events("wiki.export")
    assert len(rows) == 1 and rows[0].detail == f"scope=team team={team.id}"


def test_training_export_is_audited(tmp_path, monkeypatch):
    c, org_id, _uid = _client(tmp_path, monkeypatch)
    r = c.get("/training/export?quality=silver")
    assert r.status_code == 200
    rows = _events("training.export")
    assert len(rows) == 1 and rows[0].org_id == org_id
    # the detail carries both the quality and the example count (0 with no data), plus the IP
    assert rows[0].detail == "quality=silver count=0" and rows[0].ip == "testclient"
