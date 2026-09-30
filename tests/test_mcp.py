"""MCP client: wrapping third-party tools + the governance around them.

Model-free and SDK-free: the `mcp` SDK needs Python 3.10+ and isn't installed in the
test env, so the SDK boundary is injected (lister/caller) or mocked. These cover the
parts that must be right regardless of the SDK: tool wrapping, the approved-only gate,
the `mcp` scope, and the schema.
"""

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MCPServer


def _session(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    seed_org_and_users(s)
    s.commit()
    return s


class _Srv:
    transport = "http"
    url = "https://x/mcp"
    command = ""
    require_approval = False

    def __init__(self, name):
        self.name = name


def test_mcp_tools_for_wraps_and_dispatches():
    from anthill.agent.tools import tool_scope
    from anthill.mcp.client import mcp_tools_for

    seen = {}

    def lister(server, headers):
        return [
            {
                "name": "create_issue",
                "description": "Open an issue",
                "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
            }
        ]

    def caller(server, headers, name, args):
        seen["call"] = (name, args)
        return "created"

    tools = mcp_tools_for(_Srv("GitHub Tools"), {}, lister=lister, caller=caller)
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "mcp_github-tools_create_issue"  # prefixed + slugged, collision-safe
    assert tool_scope(t.name) == "mcp"  # falls under the mcp scope
    assert t.parameters["properties"]["title"]["type"] == "string"
    assert t.fn(title="Bug") == "created"
    assert seen["call"] == ("create_issue", {"title": "Bug"})


def test_mcp_tools_for_swallows_discovery_errors():
    from anthill.mcp.client import mcp_tools_for

    def boom(server, headers):
        raise RuntimeError("server down")

    assert mcp_tools_for(_Srv("x"), {}, lister=boom) == []  # never breaks the toolset


def test_mcp_scope_gate():
    from anthill.agent.tools import AgentPrincipal

    without = AgentPrincipal(name="a", scopes={"web", "wiki"})
    granted = AgentPrincipal(name="b", scopes={"web", "mcp"})
    assert not without.allows("mcp_github_create_issue")
    assert granted.allows("mcp_github_create_issue")


def test_mcp_client_tools_loads_only_approved(tmp_path, monkeypatch):
    import anthill.mcp as mcp_pkg
    from anthill.agent.tools import Tool

    monkeypatch.setattr(mcp_pkg, "mcp_available", lambda: True)
    monkeypatch.setattr(
        mcp_pkg,
        "mcp_tools_for",
        lambda s, h: [
            Tool(name=f"mcp_{s.name}_ping", description="", parameters={}, fn=lambda **k: "ok")
        ],
    )
    s = _session(tmp_path)
    s.add(MCPServer(org_id=1, name="alpha", transport="http", url="a", status="approved"))
    s.add(MCPServer(org_id=1, name="beta", transport="http", url="b", status="pending"))
    s.add(MCPServer(org_id=1, name="gamma", transport="http", url="c", status="disabled"))
    s.commit()

    from anthill.web.mcp_store import mcp_client_tools

    names = {t.name for t in mcp_client_tools(s, 1)}
    assert names == {"mcp_alpha_ping"}  # approved only; pending + disabled excluded


def test_mcp_client_tools_empty_without_sdk(tmp_path, monkeypatch):
    import anthill.mcp as mcp_pkg

    monkeypatch.setattr(mcp_pkg, "mcp_available", lambda: False)
    s = _session(tmp_path)
    s.add(MCPServer(org_id=1, name="alpha", status="approved", url="a"))
    s.commit()
    from anthill.web.mcp_store import mcp_client_tools

    assert mcp_client_tools(s, 1) == []  # degrades cleanly when the SDK is absent


def test_mcp_servers_table_and_roundtrip(tmp_path):
    s = _session(tmp_path)
    assert "mcp_servers" in inspect(s.get_bind()).get_table_names()
    s.add(MCPServer(org_id=1, name="x", transport="stdio", command="npx srv", status="pending"))
    s.commit()
    row = s.query(MCPServer).one()
    assert row.status == "pending" and row.transport == "stdio"
