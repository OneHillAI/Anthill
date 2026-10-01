"""MCP Integrations (Slack, GitHub, Google Drive, ...) is not an org-only feature: /connectors/mcp
only requires admin, which every Solo account's own user already is. It used to be reachable only
through the Organisation tab's org-converted branch ("See everything in Manage organisation"), so a
Solo account - the common case - had no link to it anywhere and had to already know the URL (founder
report, 2026-10-01: "the mcp connectors... it was built in, now it seems gone"). It is now its own
Settings tab, same as Model/Knowledge/Personality/Privacy/This device/Organisation, reachable
whether or not the account has ever converted to an org.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MCPServer, Organization, OrgSettings, User


def _client(tmp_path, monkeypatch):
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
    o = Organization(name="Solo", slug="s")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo", ollama_model="qwen2.5:7b")])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def test_solo_settings_has_an_integrations_tab_linking_to_connectors(tmp_path, monkeypatch):
    c, _app, _org_id = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'data-st="integrations"' in body  # a real tab, not folded into Organisation
    assert 'href="/connectors/mcp"' in body  # links straight to the connector gallery
    # UI/UX review, 2026-10-01: every other card header on this page explains its jargon inline -
    # Integrations was the one exception, and "MCP" is exactly the term that needs it.
    integrations_panel = body.split('data-st="integrations"', 2)[2]
    assert 'class="help"' in integrations_panel and "Model Context Protocol" in integrations_panel


def test_solo_account_can_actually_load_the_connectors_page(tmp_path, monkeypatch):
    # The route only ever required admin - every Solo account's own user already is one - so this
    # always worked once you knew the URL; the bug was that nothing in the UI ever gave you the URL.
    c, _app, _org_id = _client(tmp_path, monkeypatch)
    r = c.get("/connectors/mcp")
    assert r.status_code == 200
    assert "Slack" in r.text and "Google Drive" in r.text and "GitHub" in r.text


def test_integrations_tab_shows_in_the_settings_sidebar_subrail(tmp_path, monkeypatch):
    c, _app, _org_id = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert '<a href="/personalize#integrations">Integrations</a>' in body


def test_connectors_page_back_link_points_to_settings_not_manage_organisation(
    tmp_path, monkeypatch
):
    # Previously every /connectors/mcp visit got "<- Manage organisation" (_ORG_SUBSETTINGS), which
    # is actively wrong once Solo links in directly from its own Integrations tab.
    c, _app, _org_id = _client(tmp_path, monkeypatch)
    body = c.get("/connectors/mcp").text
    assert 'href="/personalize#integrations"' in body
    assert 'title="Back to Settings"' in body
    assert "Manage organisation" not in body


def test_integrations_status_pill_reflects_connected_count(tmp_path, monkeypatch):
    c, app_mod, org_id = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Not set up" in body
    s = app_mod._SessionFactory()
    s.add(
        MCPServer(
            org_id=org_id,
            name="GitHub",
            transport="http",
            url="https://example.com/mcp",
            status="approved",
        )
    )
    s.commit()
    body = c.get("/personalize").text
    assert "1 connected" in body
