"""MCP server protocol: expose the org brain (wiki / cache / memory) to external
MCP clients over a minimal, spec-compliant JSON-RPC 2.0 surface
(`initialize` / `tools/list` / `tools/call`).

Pure and dependency-light: this module decides *what* is exposed and shapes the
JSON-RPC, but the actual query execution + access logging are injected by the web
layer via `run_tool` (so this stays testable and free of web/db imports). Exposure is
OFF by default and per-resource; the web layer gates auth and records every call.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"

# resource key -> (exposed tool name, description)
RESOURCES = {
    "wiki": ("query_wiki", "Answer a question from the organization's wiki and cache."),
    "cache": ("search_cache", "Look up a cached answer to a question, if one exists."),
    "memory": ("recall_memory", "Recall durable organization memory relevant to a query."),
}


# Agent-to-agent + task-deferral tools. Offered ONLY to a caller authenticated as an
# AgentIdentity holding the `a2a` scope (see web/a2a.py); dispatch + governance live in
# the web layer. Intra-org only - never cross-org.
A2A_TOOLS = [
    {
        "name": "a2a_ask_agent",
        "description": "Ask another agent in this organization a question; returns its answer.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent": {"type": "string"}, "question": {"type": "string"}},
            "required": ["agent", "question"],
        },
    },
    {
        "name": "a2a_delegate",
        "description": "Delegate a goal to another agent in this organization; it runs under "
        "its own identity and scopes and returns the result.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent": {"type": "string"}, "goal": {"type": "string"}},
            "required": ["agent", "goal"],
        },
    },
    {
        "name": "a2a_defer_task",
        "description": "Queue a task to run later via the scheduler (hand-off / deferral).",
        "inputSchema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}, "schedule": {"type": "string"}},
            "required": ["goal"],
        },
    },
]


class ToolRefused(Exception):
    """A tool call we refuse, or a bad request, with a reason that we wrote ourselves. Only this class carries its
    text to the caller; any other exception becomes the fixed "Tool failed." and is logged."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class ToolInvalid(ToolRefused, ValueError):
    """A bad request ("'goal' is required")."""

    def __init__(self, reason: str):
        super().__init__(-32602, f"Invalid params: {reason}")


class ToolDenied(ToolRefused, PermissionError):
    """A refusal by our own permission check."""

    def __init__(self, reason: str):
        super().__init__(-32001, f"Not allowed: {reason}")


def exposed_resources(cfg) -> list:
    """Enabled resource keys for this org ([] if the MCP server is off)."""
    if not getattr(cfg, "mcp_server_enabled", False):
        return []
    keys = []
    if getattr(cfg, "mcp_expose_wiki", False):
        keys.append("wiki")
    if getattr(cfg, "mcp_expose_cache", False):
        keys.append("cache")
    if getattr(cfg, "mcp_expose_memory", False):
        keys.append("memory")
    return keys


def tool_specs(cfg) -> list:
    """MCP tool descriptors for the enabled resources."""
    specs = []
    for key in exposed_resources(cfg):
        name, desc = RESOURCES[key]
        specs.append(
            {
                "name": name,
                "description": desc,
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        )
    return specs


def name_to_kind(name: str):
    for key, (tname, _desc) in RESOURCES.items():
        if tname == name:
            return key
    return None


def handle_jsonrpc(payload: dict, cfg, *, run_tool, a2a_tools=None, a2a_call=None):
    """Handle one JSON-RPC request against the org's exposed resources.

    `run_tool(kind, query) -> str` is injected by the caller (it executes the query
    and logs access). When the caller is authenticated as an agent identity, the web
    layer also passes `a2a_tools` (extra tool specs) + `a2a_call(name, arguments) -> str`
    for governed agent-to-agent + task deferral. Returns a JSON-RPC response dict, or
    None for a notification.
    """
    rid = payload.get("id")
    method = payload.get("method")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "anthill", "version": "0.1.0"},
            }
        )
    if method and method.startswith("notifications/"):
        return None  # notifications carry no response
    if method == "tools/list":
        return ok({"tools": tool_specs(cfg) + list(a2a_tools or [])})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        kind = name_to_kind(name)
        if kind and kind in exposed_resources(cfg):
            try:
                text = run_tool(kind, str(args.get("query", "")))
            except Exception:
                log.exception("MCP tool %s failed", name)  # the detail stays in the server log
                return err(-32603, "Tool failed.")
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        if a2a_call is not None and name in {t["name"] for t in (a2a_tools or [])}:
            try:
                text = a2a_call(name, args)
            except ToolRefused as e:  # a refusal or a bad request that we worded ourselves, never an internal failure
                return err(e.code, e.reason)
            except Exception:
                log.exception("A2A tool %s failed", name)
                return err(-32603, "Tool failed.")
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        return err(-32601, f"Tool not available: {name}")
    return err(-32601, f"Method not found: {method}")
