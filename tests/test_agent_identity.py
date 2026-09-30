"""Agent identity: scope mapping, least-privilege enforcement, audit hook."""

from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import (
    DEFAULT_AGENT_SCOPES,
    AgentPrincipal,
    make_tools,
    tool_scope,
)


def test_tool_scope_mapping():
    assert tool_scope("web_search") == "web"
    assert tool_scope("search_wiki") == "wiki"
    assert tool_scope("read_emails") == "email"
    assert tool_scope("create_pdf") == "docs"
    assert tool_scope("mcp_slack_post") == "mcp"  # external connectors are MCP tools now
    assert tool_scope("slack_post_message") == "other"  # bespoke connector scopes removed


def test_principal_allows_within_scope():
    p = AgentPrincipal(name="bot", scopes={"web", "wiki"})
    assert p.allows("web_search")
    assert p.allows("search_wiki")
    assert not p.allows("read_emails")  # email scope not granted
    assert not p.allows("slack_post_message")


def test_disabled_principal_blocks_everything():
    p = AgentPrincipal(name="bot", scopes={"web", "wiki"}, active=False)
    assert not p.allows("web_search")


def test_default_scopes_are_least_privilege():
    # external write connectors must NOT be in the default grant
    assert "email" not in DEFAULT_AGENT_SCOPES
    assert "slack" not in DEFAULT_AGENT_SCOPES
    assert "msgraph" not in DEFAULT_AGENT_SCOPES
    assert {"web", "wiki", "files", "docs"} == DEFAULT_AGENT_SCOPES


def test_executor_authorize_blocks_and_audits():
    calls = []
    p = AgentPrincipal(name="scheduler", scopes={"web"})  # only web
    ex = AgentExecutor(
        backend=None,
        tools=make_tools(workspace="/tmp/x"),
        identity=p,
        on_action=lambda name, tool, scope, allowed: calls.append((tool, scope, allowed)),
    )
    assert ex._authorize("web_search") is True
    assert ex._authorize("read_emails") is False  # email not granted
    assert ex._authorize("slack_post_message") is False
    # every check was audited with the right verdict
    assert ("web_search", "web", True) in calls
    assert ("read_emails", "email", False) in calls


def test_executor_without_identity_allows_all():
    ex = AgentExecutor(backend=None, tools=make_tools(workspace="/tmp/x"))
    assert ex._authorize("read_emails") is True  # no identity → no restriction
