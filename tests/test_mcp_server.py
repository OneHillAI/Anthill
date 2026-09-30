"""MCP server: exposing the org brain (Anthill as MCP server).

Covers the governance that must be right: exposure is off by default, only enabled
resources are listed, auth is required, and every call is logged. Model-free - the
org-brain lookup (`_answer`) is stubbed so no backend is needed.
"""

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.crypto import encrypt
from anthill.web.db import MCPAccessLog, MCPConsumer, Organization, OrgSettings


class _Cfg:
    def __init__(self, **k):
        self.mcp_server_enabled = k.get("enabled", False)
        self.mcp_expose_wiki = k.get("wiki", False)
        self.mcp_expose_cache = k.get("cache", False)
        self.mcp_expose_memory = k.get("memory", False)


def test_exposed_off_by_default():
    from anthill.mcp.server import exposed_resources

    assert exposed_resources(_Cfg(enabled=False, wiki=True)) == []  # off wins


def test_tool_specs_only_enabled():
    from anthill.mcp.server import exposed_resources, tool_specs

    cfg = _Cfg(enabled=True, wiki=True, memory=True)
    assert set(exposed_resources(cfg)) == {"wiki", "memory"}
    assert {t["name"] for t in tool_specs(cfg)} == {"query_wiki", "recall_memory"}


def test_handle_jsonrpc_dispatch_and_gate():
    from anthill.mcp.server import handle_jsonrpc

    cfg = _Cfg(enabled=True, wiki=True)  # cache + memory NOT exposed
    init = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, cfg, run_tool=lambda k, q: ""
    )
    assert init["result"]["serverInfo"]["name"] == "anthill"

    seen = {}

    def rt(kind, q):
        seen["c"] = (kind, q)
        return "ANSWER"

    call = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "query_wiki", "arguments": {"query": "hi"}},
        },
        cfg,
        run_tool=rt,
    )
    assert call["result"]["content"][0]["text"] == "ANSWER" and seen["c"] == ("wiki", "hi")

    # a tool whose resource is not exposed is rejected
    blocked = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_cache", "arguments": {"query": "x"}},
        },
        cfg,
        run_tool=rt,
    )
    assert "error" in blocked


def _app(tmp_path):
    import anthill.web.app as app_mod

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return app_mod


def test_mcp_endpoint_auth_list_and_logging(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.mcp_store as store

    monkeypatch.setattr(store, "_answer", lambda db_, org_id, kind, q: f"answer:{kind}:{q}")
    app_mod = _app(tmp_path)
    try:
        s = app_mod._SessionFactory()
        o = Organization(name="A", slug="a")
        s.add(o)
        s.flush()
        s.add(OrgSettings(org_id=o.id, mcp_server_enabled=True, mcp_expose_wiki=True))
        s.add(
            MCPConsumer(
                org_id=o.id,
                name="c1",
                token_enc=encrypt("tok123"),
                scopes="wiki",
                status="approved",
            )
        )
        s.commit()
        c = TestClient(app_mod.app)
        H = {"Authorization": "Bearer tok123"}

        assert (
            c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code
            == 401
        )
        assert (
            c.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": "Bearer wrong"},
            ).status_code
            == 401
        )

        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=H)
        assert [t["name"] for t in r.json()["result"]["tools"]] == ["query_wiki"]

        r2 = c.post(
            "/mcp",
            headers=H,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "query_wiki", "arguments": {"query": "hello"}},
            },
        )
        assert r2.json()["result"]["content"][0]["text"] == "answer:wiki:hello"
        assert app_mod._SessionFactory().query(MCPAccessLog).count() == 1  # exposure logged
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_mcp_server_schema(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    db.create_tables(eng)
    ins = inspect(eng)
    assert {"mcp_access_log", "mcp_consumers"} <= set(ins.get_table_names())
    cols = {c["name"] for c in ins.get_columns("org_settings")}
    assert {
        "mcp_server_enabled",
        "mcp_expose_wiki",
        "mcp_expose_cache",
        "mcp_expose_memory",
        "mcp_access_token_enc",
        "mcp_review_mode",
    } <= cols
    logcols = {c["name"] for c in ins.get_columns("mcp_access_log")}
    assert {"consumer_id", "allowed", "reason", "query", "resources", "result_summary"} <= logcols


def _gov_cfg(**over):
    from types import SimpleNamespace

    base = {
        "mcp_server_enabled": True,
        "mcp_expose_wiki": True,
        "mcp_expose_cache": True,
        "mcp_expose_memory": True,
        "mcp_review_mode": "review",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _gov_consumer(**over):
    from types import SimpleNamespace

    base = {"id": 1, "name": "c", "status": "approved", "scopes": "wiki,cache,memory"}
    base.update(over)
    return SimpleNamespace(**base)


def test_authorize_query_is_default_deny():
    from anthill.web.mcp_store import authorize_query

    # denied paths
    assert authorize_query(_gov_cfg(mcp_server_enabled=False), _gov_consumer(), "wiki")[0] is False
    assert authorize_query(_gov_cfg(mcp_expose_wiki=False), _gov_consumer(), "wiki")[0] is False
    assert authorize_query(_gov_cfg(), None, "wiki")[0] is False  # unknown consumer
    assert authorize_query(_gov_cfg(), _gov_consumer(status="revoked"), "wiki")[0] is False
    assert authorize_query(_gov_cfg(), _gov_consumer(scopes="cache"), "wiki")[0] is False  # scope
    assert (
        authorize_query(_gov_cfg(), _gov_consumer(status="pending"), "wiki")[0] is False
    )  # review
    # allowed paths
    assert authorize_query(_gov_cfg(), _gov_consumer(status="approved"), "wiki")[0] is True
    # log_only relaxes the pending hold
    assert (
        authorize_query(
            _gov_cfg(mcp_review_mode="log_only"), _gov_consumer(status="pending"), "wiki"
        )[0]
        is True
    )


def test_serve_logs_and_audits_every_query(tmp_path, monkeypatch):
    import anthill.web.mcp_store as store
    from anthill.web.db import AuditLog

    monkeypatch.setattr(store, "_answer", lambda db_, org_id, kind, q: "ANSWER")
    eng = create_engine(f"sqlite:///{tmp_path / 'g.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(
        OrgSettings(
            org_id=o.id, mcp_server_enabled=True, mcp_expose_wiki=True, mcp_review_mode="review"
        )
    )
    approved = MCPConsumer(
        org_id=o.id, name="ok", token_enc=encrypt("t"), scopes="wiki", status="approved"
    )
    pending = MCPConsumer(
        org_id=o.id, name="no", token_enc=encrypt("u"), scopes="wiki", status="pending"
    )
    s.add_all([approved, pending])
    s.commit()

    assert store.serve_org_query(s, o.id, approved, "wiki", "hi") == "ANSWER"
    denied = store.serve_org_query(
        s, o.id, pending, "wiki", "hi"
    )  # review holds a pending consumer
    assert denied.startswith("(denied")  # never returns org data

    logs = s.query(MCPAccessLog).order_by(MCPAccessLog.id).all()
    assert len(logs) == 2  # EVERY query logged (allow and deny)
    assert logs[0].allowed is True and logs[0].result_summary == "ANSWER"
    assert logs[0].consumer_id == approved.id
    assert logs[1].allowed is False and "pending" in logs[1].reason
    assert s.query(AuditLog).filter(AuditLog.event == "mcp.query").count() == 2  # mirrored to audit
