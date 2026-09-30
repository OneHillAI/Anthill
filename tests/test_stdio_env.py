"""Encrypted env for stdio connectors (one-click Slack): decrypt_env round-trip, the add route
storing env encrypted, and the Slack catalog entry being valid + one-click."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.connectors import catalog_by_id, validate_entry
from anthill.web import db, mcp_store
from anthill.web.crypto import encrypt
from anthill.web.db import MCPServer, Organization, User


def test_decrypt_env_round_trip():
    srv = MCPServer(
        env_enc=encrypt(json.dumps({"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_TEAM_ID": "T1"}))
    )
    assert mcp_store.decrypt_env(srv) == {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_TEAM_ID": "T1"}
    assert mcp_store.decrypt_env(MCPServer(env_enc="")) == {}  # none stored


def test_slack_catalog_entry_is_one_click_with_env_fields():
    slack = catalog_by_id("slack")
    assert validate_entry(slack) == []  # env-target field accepted
    assert slack["guided"] is True
    targets = {f["target"] for f in slack["auth"]["fields"]}
    envs = {f.get("env") for f in slack["auth"]["fields"]}
    assert targets == {"env"} and "SLACK_BOT_TOKEN" in envs


def test_add_stores_env_encrypted(tmp_path):
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
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))

    r = c.post(
        "/mcp/servers",
        data={
            "name": "Slack",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-slack",
            "catalog_id": "slack",
            "env_json": json.dumps({"SLACK_BOT_TOKEN": "xoxb-secret", "SLACK_TEAM_ID": "T9"}),
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    row = app_mod._SessionFactory().query(MCPServer).filter(MCPServer.org_id == o.id).one()
    assert row.env_enc and "xoxb-secret" not in row.env_enc  # stored, and encrypted (not plaintext)
    assert mcp_store.decrypt_env(row) == {"SLACK_BOT_TOKEN": "xoxb-secret", "SLACK_TEAM_ID": "T9"}
