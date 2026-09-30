"""Read documents from a connected document/storage MCP server.

This is the shared layer behind "import a file into the wiki" and the file pickers in tasks
and chat. Document connectors (catalog `provides_files`) expose different tool names, so we
resolve which tool lists and which reads two ways: an explicit `files` mapping in the catalog
entry when we know the schema (e.g. Filesystem), otherwise a heuristic over the server's live
tool names. Every call is tolerant: a missing tool, an offline runtime, or an unparseable
result yields an empty list / empty string, never an exception - a flaky connector must not
break the wiki page it sits on.

Nothing here writes anything: callers feed the returned text through the normal review gate
(`propose_wiki_write`), so an imported document is a pending draft until a human approves it.
"""

from __future__ import annotations

import json
from typing import Any

from ..connectors.catalog import catalog_by_id, doc_source_ids
from ..mcp import call_tool_raw, list_tools_raw, mcp_available
from .mcp_store import decrypt_headers

# Tool-name hints for connectors without an explicit catalog `files` mapping.
_LIST_HINTS = ("list", "search", "browse", "find", "query")
_READ_HINTS = ("read", "get", "retrieve", "fetch", "content", "cat", "open", "view")

_MAX_ITEMS = 200


def doc_source_servers(db, org_id: int) -> list[Any]:
    """Approved MCP servers that are document sources (their catalog entry sets provides_files)."""
    from .db import MCPServer

    ids = doc_source_ids()
    rows = (
        db.query(MCPServer)
        .filter(MCPServer.org_id == org_id, MCPServer.status == "approved")
        .order_by(MCPServer.name)
        .all()
    )
    return [s for s in rows if str(s.catalog_id or "") in ids]


def _files_map(server) -> dict[str, Any]:
    entry = catalog_by_id(str(getattr(server, "catalog_id", "") or ""))
    files = (entry or {}).get("files")
    return files if isinstance(files, dict) else {}


def _resolve_tool(server, kind: str) -> tuple[str | None, str]:
    """Return (tool_name, arg_name) for kind in {list, search, read}.

    Prefer the catalog `files` mapping; fall back to matching the server's live tool names
    against the hints (and infer the argument from the tool's input schema).
    """
    fmap = _files_map(server)
    spec = fmap.get(kind)
    if isinstance(spec, dict) and spec.get("tool"):
        return str(spec["tool"]), str(spec.get("arg", ""))
    try:
        tools = list_tools_raw(server, decrypt_headers(server))
    except Exception:
        return None, ""
    hints = _READ_HINTS if kind == "read" else _LIST_HINTS
    for t in tools:
        name = str(t.get("name") or "")
        if any(h in name.lower() for h in hints):
            props = ((t.get("inputSchema") or {}).get("properties")) or {}
            arg = next(iter(props), "") if isinstance(props, dict) else ""
            return name, str(arg)
    return None, ""


def _parse_listing(out: str) -> list[dict[str, str]]:
    """Normalise a list/search tool result into [{'label','ref'}]. Tolerant of JSON or text."""
    out = (out or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        data = None
    items: list[dict[str, str]] = []
    if isinstance(data, list):
        for d in data:
            if isinstance(d, str) and d.strip():
                items.append({"label": d.strip(), "ref": d.strip()})
            elif isinstance(d, dict):
                ref = d.get("path") or d.get("id") or d.get("uri") or d.get("name") or ""
                if ref:
                    label = d.get("name") or d.get("title") or ref
                    items.append({"label": str(label), "ref": str(ref)})
        if items:
            return items[:_MAX_ITEMS]
    lines = [ln.strip(" -\t") for ln in out.splitlines() if ln.strip()]
    return [{"label": ln, "ref": ln} for ln in lines[:_MAX_ITEMS]]


def list_documents(server, query: str = "") -> list[dict[str, str]]:
    """Best-effort list of documents on a connected server. Never raises."""
    if not mcp_available():
        return []
    kind = "search" if query else "list"
    tool, arg = _resolve_tool(server, kind)
    if not tool and kind == "search":  # no search tool: fall back to a plain listing
        tool, arg = _resolve_tool(server, "list")
    if not tool:
        return []
    args = {arg: query} if arg else {}
    try:
        out = call_tool_raw(server, decrypt_headers(server), tool, args)
    except Exception:
        return []
    return _parse_listing(out)


def read_document(server, ref: str) -> str:
    """Best-effort text of one document. Returns '' on any failure."""
    if not mcp_available() or not ref:
        return ""
    tool, arg = _resolve_tool(server, "read")
    if not tool:
        return ""
    args = {arg: ref} if arg else {}
    try:
        return call_tool_raw(server, decrypt_headers(server), tool, args) or ""
    except Exception:
        return ""
