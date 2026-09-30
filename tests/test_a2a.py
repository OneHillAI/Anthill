"""Governed intra-org A2A + task deferral over MCP: scope mapping, JSON-RPC routing,
identity tokens, the serve_a2a dispatch, and the /mcp endpoint integration."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent.tools import AgentPrincipal, tool_scope
from anthill.mcp.server import A2A_TOOLS, handle_jsonrpc
from anthill.web import a2a, db
from anthill.web.db import AgentIdentity, Organization, ScheduledTask, User

# ── scope + principal (pure) ──────────────────────────────────────────────────


def test_a2a_scope_mapping_and_gate():
    assert tool_scope("a2a_defer_task") == "a2a"
    assert tool_scope("a2a_delegate") == "a2a"
    assert AgentPrincipal(name="x", scopes={"a2a"}).allows("a2a_delegate")
    assert not AgentPrincipal(name="y", scopes={"web"}).allows("a2a_delegate")
    assert not AgentPrincipal(name="z", scopes={"a2a"}, active=False).allows("a2a_defer_task")


# ── JSON-RPC routing (pure) ───────────────────────────────────────────────────


class _Cfg:  # org-brain exposure off; only the a2a tools are in play
    mcp_server_enabled = False


def test_jsonrpc_lists_a2a_tools_when_offered():
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        _Cfg(),
        run_tool=lambda k, q: "",
        a2a_tools=A2A_TOOLS,
        a2a_call=lambda n, a: "ok",
    )
    names = [t["name"] for t in resp["result"]["tools"]]
    assert {"a2a_ask_agent", "a2a_delegate", "a2a_defer_task"} <= set(names)


def test_jsonrpc_dispatches_a2a_call():
    seen = {}

    def a2a_call(name, args):
        seen["name"], seen["args"] = name, args
        return "done"

    resp = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "a2a_defer_task", "arguments": {"goal": "x"}},
        },
        _Cfg(),
        run_tool=lambda k, q: "",
        a2a_tools=A2A_TOOLS,
        a2a_call=a2a_call,
    )
    assert seen["name"] == "a2a_defer_task"
    assert resp["result"]["content"][0]["text"] == "done"


def test_jsonrpc_a2a_permission_error_becomes_jsonrpc_error():
    def a2a_call(name, args):
        raise PermissionError("nope")

    resp = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "a2a_delegate", "arguments": {"agent": "a", "goal": "b"}},
        },
        _Cfg(),
        run_tool=lambda k, q: "",
        a2a_tools=A2A_TOOLS,
        a2a_call=a2a_call,
    )
    assert "error" in resp and "Not allowed" in resp["error"]["message"]


def test_jsonrpc_a2a_unavailable_without_caller():
    # org token (no a2a_call) -> the a2a tool is not available
    resp = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "a2a_defer_task", "arguments": {"goal": "x"}},
        },
        _Cfg(),
        run_tool=lambda k, q: "",
    )
    assert "error" in resp


# ── tokens + serve_a2a dispatch (DB) ──────────────────────────────────────────


def _sess(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    return s, o.id


def test_identity_token_roundtrip(tmp_path):
    s, org_id = _sess(tmp_path)
    ident = AgentIdentity(org_id=org_id, name="bot", scopes="a2a", active=True)
    s.add(ident)
    s.commit()
    token = a2a.mint_identity_token(s, ident)
    match = a2a.identity_for_mcp_token(s, token)
    assert match is not None and match[0] == org_id and match[1].id == ident.id
    assert a2a.identity_for_mcp_token(s, "not-a-real-token") is None


def test_serve_a2a_denied_without_scope(tmp_path):
    s, org_id = _sess(tmp_path)
    caller = AgentPrincipal(name="bot", scopes={"web"})  # no a2a
    with pytest.raises(PermissionError):
        a2a.serve_a2a(s, org_id, caller, "a2a_defer_task", {"goal": "x"})


def test_serve_a2a_defer_creates_task(tmp_path):
    s, org_id = _sess(tmp_path)
    caller = AgentPrincipal(name="bot", scopes={"a2a"})
    out = a2a.serve_a2a(
        s, org_id, caller, "a2a_defer_task", {"goal": "nightly report", "schedule": "daily"}
    )
    assert "queued" in out.lower()
    assert s.query(ScheduledTask).filter(ScheduledTask.org_id == org_id).count() == 1


def test_serve_a2a_defer_rejects_invalid_schedule(tmp_path):
    s, org_id = _sess(tmp_path)
    caller = AgentPrincipal(name="bot", scopes={"a2a"})
    with pytest.raises(ValueError, match="schedule"):
        a2a.serve_a2a(
            s,
            org_id,
            caller,
            "a2a_defer_task",
            {"goal": "nightly report", "schedule": "25:99"},
        )
    assert s.query(ScheduledTask).count() == 0


def test_serve_a2a_target_not_found(tmp_path):
    s, org_id = _sess(tmp_path)
    caller = AgentPrincipal(name="bot", scopes={"a2a"})
    with pytest.raises(ValueError):
        a2a.serve_a2a(s, org_id, caller, "a2a_ask_agent", {"agent": "ghost", "question": "hi"})


# ── /mcp endpoint integration ─────────────────────────────────────────────────


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(User(org_id=o.id, email="u@a.com", role="admin", active=True))
    s.commit()
    return TestClient(app_mod.app), app_mod, o.id


def _mint(app_mod, org_id, scopes):
    s = app_mod._SessionFactory()
    ident = AgentIdentity(org_id=org_id, name="caller-bot", scopes=scopes, active=True)
    s.add(ident)
    s.commit()
    return a2a.mint_identity_token(s, ident)


def test_mcp_endpoint_identity_token_offers_and_runs_a2a(tmp_path, monkeypatch):
    c, app_mod, org_id = _client(tmp_path, monkeypatch)
    try:
        token = _mint(app_mod, org_id, "a2a")
        hdr = {"Authorization": f"Bearer {token}"}
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=hdr)
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert "a2a_defer_task" in names
        r2 = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "a2a_defer_task", "arguments": {"goal": "do the thing"}},
            },
            headers=hdr,
        )
        assert "result" in r2.json()
        s2 = app_mod._SessionFactory()
        assert s2.query(ScheduledTask).filter(ScheduledTask.org_id == org_id).count() == 1
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_mcp_endpoint_denies_a2a_without_scope(tmp_path, monkeypatch):
    c, app_mod, org_id = _client(tmp_path, monkeypatch)
    try:
        token = _mint(app_mod, org_id, "web")  # no a2a scope
        r = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "a2a_defer_task", "arguments": {"goal": "x"}},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert "error" in r.json()
        s2 = app_mod._SessionFactory()
        assert s2.query(ScheduledTask).filter(ScheduledTask.org_id == org_id).count() == 0
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
