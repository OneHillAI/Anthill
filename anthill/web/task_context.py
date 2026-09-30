"""Attach connected-service files and saved snippets to a scheduled task as run-time context.

When a task runs, anything the creator attached is read fresh and prepended to the agent's
context: files come live from the connected MCP server (so the task always sees the current
document), snippets from the saved `Snippet` rows. Tolerant by design - a connector that is
gone, unapproved, or failing simply contributes nothing, never an error that fails the task.
"""

from __future__ import annotations

import json
from typing import Any

from .db import MCPServer, Snippet
from .docsource import read_document

_MAX_CHARS = 6000  # cap per attachment so one huge doc cannot crowd out the instruction


def refs_from_form(context_files: list[str], context_snippets: list[str]) -> str:
    """Turn the form's selected values into the stored `context_refs` JSON.

    Each file value is "<server_id>|<ref>"; each snippet value is the snippet id. Returns ""
    when nothing is attached (so a plain task stays empty)."""
    files: list[dict[str, Any]] = []
    for v in context_files or []:
        sid, sep, ref = (v or "").partition("|")
        if sep and sid.isdigit() and ref.strip():
            files.append({"server_id": int(sid), "ref": ref.strip()})
    snippets = [int(x) for x in (context_snippets or []) if str(x).isdigit()]
    if not files and not snippets:
        return ""
    return json.dumps({"files": files, "snippets": snippets})


def _load(blob: str) -> dict[str, Any]:
    try:
        val = json.loads(blob or "")
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def build_task_context(db, task) -> str:
    """Assemble the attached files + snippets into a context string for the agent. Never raises."""
    refs = _load(getattr(task, "context_refs", "") or "")
    parts: list[str] = []
    for f in refs.get("files", []) or []:
        if not isinstance(f, dict):
            continue
        srv = (
            db.query(MCPServer)
            .filter(
                MCPServer.id == f.get("server_id"),
                MCPServer.org_id == task.org_id,
                MCPServer.status == "approved",
            )
            .first()
        )
        if srv is None:
            continue
        try:
            text = read_document(srv, str(f.get("ref", "")))
        except Exception:
            text = ""
        if text and not text.startswith("MCP call failed"):
            parts.append(
                f"## Attached file: {f.get('ref', '')} (from {srv.name})\n{text[:_MAX_CHARS]}"
            )
    for sid in refs.get("snippets", []) or []:
        s = db.query(Snippet).filter(Snippet.id == sid, Snippet.org_id == task.org_id).first()
        if s is not None and s.content:
            label = (s.question or "").strip()[:80]
            head = f"## Attached snippet{f': {label}' if label else ''}"
            parts.append(f"{head}\n{str(s.content)[:_MAX_CHARS]}")
    return "\n\n".join(parts)
