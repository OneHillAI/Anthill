"""MCP governance Part 2: a member can REQUEST an integration; admins are notified and approve
or reject it; the requester is notified of the outcome; only admins may approve."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MCPServer, Organization, User


def _client(tmp_path, role="member"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role=role, active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, u.id


def _srv(app_mod, org_id, uid, **over):
    s = app_mod._SessionFactory()
    base = {
        "org_id": org_id,
        "name": "Slack",
        "transport": "http",
        "url": "x",
        "status": "requested",
        "requested_by": uid,
    }
    base.update(over)
    srv = MCPServer(**base)
    s.add(srv)
    s.commit()
    return srv.id


def _get(app_mod, sid):
    return app_mod._SessionFactory().query(MCPServer).filter(MCPServer.id == sid).first()


def test_member_can_request_integration_and_admins_notified(tmp_path, monkeypatch):
    import anthill.web.push as push

    notified = {}
    monkeypatch.setattr(
        push, "send_to_org", lambda db_, org_id, payload: notified.update(org=org_id) or 1
    )
    c, app_mod, org_id, uid = _client(tmp_path, role="member")
    r = c.post(
        "/mcp/servers/request",
        data={"name": "Slack", "transport": "http", "url": "https://mcp.slack.example"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    srv = app_mod._SessionFactory().query(MCPServer).first()
    assert srv.status == "requested" and srv.requested_by == uid and srv.name == "Slack"
    assert notified.get("org") == org_id  # the org's admins were notified


def test_approve_notifies_requester(tmp_path, monkeypatch):
    import anthill.web.push as push

    sent = {}
    monkeypatch.setattr(push, "send_to_user", lambda db_, uid_, payload: sent.update(uid=uid_) or 1)
    c, app_mod, org_id, uid = _client(tmp_path, role="admin")
    sid = _srv(app_mod, org_id, uid)
    r = c.post(f"/mcp/servers/{sid}/approve", follow_redirects=False)
    assert r.status_code == 302
    assert _get(app_mod, sid).status == "approved"
    assert sent.get("uid") == uid  # requester notified


def test_reject_sets_reason_and_notifies_requester(tmp_path, monkeypatch):
    import anthill.web.push as push

    sent = {}
    monkeypatch.setattr(push, "send_to_user", lambda db_, uid_, payload: sent.update(uid=uid_) or 1)
    c, app_mod, org_id, uid = _client(tmp_path, role="admin")
    sid = _srv(app_mod, org_id, uid, name="Jira")
    r = c.post(f"/mcp/servers/{sid}/reject", data={"reason": "not needed"}, follow_redirects=False)
    assert r.status_code == 302
    srv = _get(app_mod, sid)
    assert srv.status == "rejected" and srv.reject_reason == "not needed"
    assert sent.get("uid") == uid


def test_member_cannot_approve(tmp_path):
    c, app_mod, org_id, uid = _client(tmp_path, role="member")
    sid = _srv(app_mod, org_id, uid)
    c.post(f"/mcp/servers/{sid}/approve", follow_redirects=False)
    assert _get(app_mod, sid).status == "requested"  # admin-gated: nothing changed
