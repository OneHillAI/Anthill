"""MCP governance Part 3: the member-facing Integrations page lists APPROVED servers only
(not pending/requested) and hosts the Request form."""

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
    return c, app_mod, o.id


def test_integrations_lists_approved_only_and_has_request_form(tmp_path):
    c, app_mod, org_id = _client(tmp_path, role="member")
    s = app_mod._SessionFactory()
    s.add_all(
        [
            MCPServer(
                org_id=org_id, name="SlackApproved", transport="http", url="x", status="approved"
            ),
            MCPServer(
                org_id=org_id, name="JiraPending", transport="http", url="x", status="requested"
            ),
        ]
    )
    s.commit()
    r = c.get("/integrations")
    assert r.status_code == 200
    body = r.text
    assert "SlackApproved" in body  # approved integrations are visible to members
    assert "JiraPending" not in body  # pending/requested are not shown on the member page
    assert 'action="/mcp/servers/request"' in body  # the Request form is present
