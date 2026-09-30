"""Model Context Protocol (MCP) integration.

Two moves, both governed:
  - client (client.py): the agent uses tools from approved third-party MCP servers,
    so we stop hand-writing every connector. An MCP server must be admin-approved
    before its tools load.
  - server (server.py): expose the org brain (wiki/cache/memory) to other tools.
    Off by default; the admin approves what is exposed and every access is logged.

The official `mcp` SDK requires Python 3.10+. All SDK imports are lazy, so on an
environment without it (or without the `[mcp]` extra) MCP simply reports unavailable
and the rest of the app is unaffected.
"""

from .client import call_tool_raw, list_tools_raw, mcp_available, mcp_tools_for

__all__ = ["call_tool_raw", "list_tools_raw", "mcp_available", "mcp_tools_for"]
