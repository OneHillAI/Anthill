from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..common.text import slugify

# ── permission scopes (for per-agent least-privilege, §agent identity) ─────────
# Every tool belongs to a coarse scope. An agent identity is granted a set of
# scopes; the executor blocks any tool outside them. This is the "may this agent
# touch X at all" gate; per-call human approval (needs_approval) is the finer one.

_SCOPE_BY_PREFIX = {
    "web_": "web",
    "fetch_": "web",
    "search_wiki": "wiki",
    "read_wiki": "wiki",
    "write_wiki": "wiki",
    "remember": "wiki",
    "create_": "docs",  # create_pdf / create_file / create_chart
    "read_emails": "email",
    "draft_email": "email",
    "read_file": "files",
    "list_files": "files",
    "mcp_": "mcp",  # tools from approved third-party MCP servers (the connector path)
    "a2a_": "a2a",  # governed agent-to-agent + task deferral over MCP (intra-org)
}

# Sensible least-privilege default for a freshly-created agent identity:
# local knowledge + web + files/docs, but NO external write connectors until an
# admin grants them.
DEFAULT_AGENT_SCOPES = {"web", "wiki", "files", "docs"}


def tool_scope(name: str) -> str:
    """Map a tool name to its permission scope."""
    for prefix, scope in _SCOPE_BY_PREFIX.items():
        if name == prefix or name.startswith(prefix):
            return scope
    return "other"


@dataclass
class AgentPrincipal:
    """A governed agent identity: a named actor with scoped permissions.

    Mirrors the 'agent has its own identity' model - actions are attributable to
    this principal, limited to its scopes, and blocked entirely if deactivated.
    """

    name: str
    scopes: set
    active: bool = True

    def allows(self, tool_name: str) -> bool:
        return self.active and tool_scope(tool_name) in self.scopes


@dataclass
class Tool:
    """One callable tool available to the agent."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments
    fn: Callable  # the actual function
    needs_approval: bool = False  # pause and ask human before running

    def ollama_spec(self) -> dict:
        """Ollama / OpenAI tool-call format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, arguments: dict) -> str:
        """Execute and return a string result the model can read."""
        try:
            result = self.fn(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            return f"ERROR: {e}"


# ── built-in tool implementations ─────────────────────────────────────────────


def _web_search(query: str, max_results: int = 5) -> list[dict]:
    from ..search.web import web_search

    results = web_search(query, max_results=max_results)
    return [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results]


def _fetch_url(url: str) -> str:
    from ..search.web import _fetch_body

    body = _fetch_body(url, max_chars=4000)
    return body or "(could not fetch page)"


def _read_wiki(slug: str, workspace: str = "workspace") -> str:
    # Confine to the wiki dir: slugify the caller-supplied slug (dropping any '/' or '..') so a
    # crafted slug like '../../etc/passwd' can't escape. Pages are WRITTEN under slugify(title), so a
    # legitimate slug is already slug-form and this round-trips; a traversal payload collapses to a
    # harmless in-dir name. Mirrors workspace.page()'s write-side slugify.
    safe = slugify(slug)
    path = Path(workspace) / "wiki" / f"{safe}.md"
    if not path.exists():
        return f"(page '{slug}' not found in wiki)"
    return path.read_text()


def _search_wiki(query: str, workspace: str = "workspace", k: int = 3) -> list[dict]:
    from ..cache import embedder as emb
    from ..wiki.workspace import Workspace

    ws = Workspace(Path(workspace))
    if not ws.exists():
        return []
    pages = ws.pages()
    if not pages:
        return []
    from ..search import meili

    if meili.enabled():
        by_slug = {p.stem: p for p in pages}
        hits = [
            by_slug[s] for s in meili.search_slugs(query, k, index=ws.meili_index) if s in by_slug
        ]
        if hits:
            return [{"slug": p.stem, "preview": p.read_text()[:200]} for p in hits]
    try:
        q_vec = emb.embed(query)
        scored = [(emb.cosine(q_vec, emb.embed(p.read_text()[:2000])), p) for p in pages]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"slug": p.stem, "preview": p.read_text()[:200]} for _, p in scored[:k]]
    except Exception:
        return [{"slug": p.stem, "preview": ""} for p in pages[:k]]


def _write_wiki_draft(slug: str, content: str, workspace: str = "workspace") -> str:
    """Write a draft wiki page - goes to review queue, not directly live."""
    from ..common.text import normalize_wiki_page
    from ..wiki.workspace import Workspace

    ws = Workspace(Path(workspace))
    if not ws.exists():
        return "workspace not initialised"
    page_md = normalize_wiki_page(content)
    path = ws.write_page(slug, page_md)
    ws.rebuild_index()
    ws.append_log("agent-draft", slug)
    return f"draft written to {path.relative_to(ws.root)} (pending review)"


def files_owner(org, user_id=None) -> str:
    """Canonical file-owner key. Per-user (org + user) so one org member's files are never listed,
    read, or served to another member (multi-tenant isolation; closes the within-org residual from
    PR #432). Falls back to org-only when there is no single end-user (e.g. agent-to-agent)."""
    org_s = str(org) if org not in (None, "") else "shared"
    # Slash-free key: the owner is used both as a sanitised dir (_files_dir) and as a raw path
    # segment (_files_target), so it must contain no "/" for the two to resolve to the same dir.
    return f"{org_s}-u{user_id}" if user_id not in (None, "") else org_s


def _files_dir(owner: str | None = None) -> Path:
    """Base download dir, scoped to an owner - the org, or ``org/user`` for per-user isolation (see
    ``files_owner``) - so files created for one tenant/user are never read or served to another."""
    import os
    import re

    base = Path(os.environ.get("ANTHILL_FILES_DIR", "data/files"))
    if owner:
        base = base / (re.sub(r"[^A-Za-z0-9._-]", "_", str(owner)) or "shared")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_name(name: str, fmt: str, exts) -> str:
    """basename only, sanitised, with the right extension for `fmt`, plus a short
    random token so a created file cannot be guessed/enumerated by another user."""
    import os
    import re
    import secrets

    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or "output")) or "output"
    if not base.lower().endswith(tuple(exts)):
        base = base.rsplit(".", 1)[0] + "." + fmt
    root, _, ext = base.rpartition(".")
    tok = secrets.token_hex(4)
    return f"{root}-{tok}.{ext}" if ext else f"{base}-{tok}"


def _create_file(
    content: str, filename: str = "output", format: str = "pdf", *, base_dir: Path | None = None
) -> str:
    """Create a downloadable file (pdf/docx/pptx/xlsx/html/md/txt) from content."""
    from ..multimodal.files import SUPPORTED, create

    fmt = (format or "pdf").lower().lstrip(".")
    if fmt not in SUPPORTED:
        return f"Unsupported format '{fmt}'. Use one of: {', '.join(SUPPORTED)}"
    out = (base_dir or _files_dir()) / _safe_name(filename, fmt, ["." + fmt])
    try:
        create(content, fmt, out)
    except ImportError as e:
        return str(e)
    return f"Created {out.name} ({out.stat().st_size:,} bytes). Download: /files/{out.name}"


def _create_chart(
    labels, values, filename: str = "chart.png", title: str = "", *, base_dir: Path | None = None
) -> str:
    """Create a bar-chart image (png/jpg) from labels + values."""
    from ..multimodal.images import bar_chart

    out = (base_dir or _files_dir()) / _safe_name(filename, "png", [".png", ".jpg", ".jpeg"])
    try:
        bar_chart(labels, values, out, title=title)
    except Exception as e:
        return f"Could not create chart: {e}"
    return f"Created {out.name} ({out.stat().st_size:,} bytes). Download: /files/{out.name}"


def _remember(fact: str, workspace: str = "workspace") -> str:
    """Append a quick note to the wiki log."""
    from ..wiki.workspace import Workspace

    ws = Workspace(Path(workspace))
    if ws.exists():
        ws.append_log("agent-note", fact[:100])
    return f"noted: {fact}"


def _list_files(directory: str = ".", *, base_dir: Path | None = None) -> list[str]:
    base = base_dir or _files_dir()
    if not base.exists():
        return []
    return [f.name for f in base.iterdir() if f.is_file()][:30]


def _read_file(path: str, *, base_dir: Path | None = None) -> str:
    """Read a file the agent created or received. Confined to the agent's own files
    dir: only a bare filename in that dir is allowed - never a path, traversal, or
    absolute path - otherwise prompt-injected content could read secrets (.env, keys,
    ~/.ssh) and exfiltrate them."""
    import os

    from ..multimodal.reader import read_file

    name = str(path)
    if name in ("", ".", "..") or name != os.path.basename(name):
        return "read_file: access denied - name a file in your workspace, not a path."
    base = (base_dir or _files_dir()).resolve()
    target = (base / name).resolve()
    if target.parent != base or not target.is_file():
        return f"read_file: no such file '{name}'."
    return read_file(target).text[:6000]


# ── email reading (configure with ANTHILL_EMAIL_* env vars) ────────────────────


def _read_emails(max_count: int = 10) -> list[dict]:
    """Read recent emails via IMAP.

    Configure: ANTHILL_EMAIL_HOST, ANTHILL_EMAIL_USER, ANTHILL_EMAIL_PASSWORD
    Returns stub data if not configured.
    """
    import os

    host = os.environ.get("ANTHILL_EMAIL_HOST", "")
    if not host:
        return [
            {
                "note": "Email not configured. Set ANTHILL_EMAIL_HOST, "
                "ANTHILL_EMAIL_USER, ANTHILL_EMAIL_PASSWORD."
            }
        ]
    import email as emaillib
    import imaplib

    user = os.environ.get("ANTHILL_EMAIL_USER", "")
    pw = os.environ.get("ANTHILL_EMAIL_PASSWORD", "")
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, pw)
        M.select("INBOX")
        _, data = M.search(None, "UNSEEN")
        ids = data[0].split()[-max_count:]
        results = []
        for uid in ids:
            _, raw = M.fetch(uid, "(RFC822)")
            msg = emaillib.message_from_bytes(raw[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")[:500]
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")[:500]
            results.append(
                {
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "date": msg.get("Date", ""),
                    "body_preview": body,
                }
            )
        M.logout()
        return results
    except Exception as e:
        return [{"error": str(e)}]


def _draft_email(to: str, subject: str, body: str) -> str:
    """Return a formatted email draft (does NOT send - human reviews first)."""
    return (
        f"DRAFT EMAIL\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"---\n{body}\n"
        f"---\n"
        f"(Review and send manually - this tool only drafts, it never sends)"
    )


# ── catalogue ─────────────────────────────────────────────────────────────────


# The only two tools that reach the open internet (as opposed to the org's own wiki, the user's own
# files, or a connected email account) - the set a caller excludes to honor an off "Web search" toggle.
WEB_TOOLS = frozenset({"web_search", "fetch_url"})


def make_tools(
    workspace: str = "workspace", owner: str | None = None, *, exclude: set[str] | None = None
) -> list[Tool]:
    """Return the full set of built-in tools, bound to a workspace and (optionally)
    an owner. File tools read and write only inside that owner's download dir.

    ``exclude`` drops named tools from the returned list (e.g. ``WEB_TOOLS`` when a caller's own
    "Web search" toggle is off) - the model then has no way to reach the internet at all, agent mode
    included, rather than the toggle only affecting the non-agent chat path's own web augmentation."""

    _fdir = _files_dir(owner)

    def _rw(slug, content):
        return _write_wiki_draft(slug, content, workspace)

    def _sw(query, k=3):
        return _search_wiki(query, workspace, k)

    def _rkw(slug):
        return _read_wiki(slug, workspace)

    def _rem(fact):
        return _remember(fact, workspace)

    def _rf(path):
        return _read_file(path, base_dir=_fdir)

    def _cf(content, filename="output", format="pdf"):
        return _create_file(content, filename, format, base_dir=_fdir)

    def _cc(labels, values, filename="chart.png", title=""):
        return _create_chart(labels, values, filename, title, base_dir=_fdir)

    def _lf(directory="."):
        return _list_files(directory, base_dir=_fdir)

    _tools = [
        Tool(
            "web_search",
            "Search the internet for current information.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            _web_search,
        ),
        Tool(
            "fetch_url",
            "Fetch the text content of a web page.",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            _fetch_url,
        ),
        Tool(
            "search_wiki",
            "Find the most relevant pages in the organisation wiki.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 3}},
                "required": ["query"],
            },
            _sw,
        ),
        Tool(
            "read_wiki",
            "Read the full content of a wiki page by slug.",
            {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
            _rkw,
        ),
        Tool(
            "write_wiki_draft",
            "Write a new or updated wiki page. Goes to admin review queue.",
            {
                "type": "object",
                "properties": {"slug": {"type": "string"}, "content": {"type": "string"}},
                "required": ["slug", "content"],
            },
            _rw,
        ),
        Tool(
            "create_file",
            "Create a downloadable file from text/markdown content. "
            "format is one of pdf, docx, pptx, xlsx, html, md, txt.",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "filename": {"type": "string", "default": "output"},
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "pptx", "xlsx", "html", "md", "txt"],
                        "default": "pdf",
                    },
                },
                "required": ["content"],
            },
            _cf,
        ),
        Tool(
            "create_chart",
            "Create a bar-chart image (png or jpg) from labels and values.",
            {
                "type": "object",
                "properties": {
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "values": {"type": "array", "items": {"type": "number"}},
                    "filename": {"type": "string", "default": "chart.png"},
                    "title": {"type": "string", "default": ""},
                },
                "required": ["labels", "values"],
            },
            _cc,
        ),
        Tool(
            "read_emails",
            "Read recent unread emails from the configured inbox.",
            {"type": "object", "properties": {"max_count": {"type": "integer", "default": 10}}},
            _read_emails,
        ),
        Tool(
            "draft_email",
            "Compose an email draft for human review.",
            {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            _draft_email,
            needs_approval=True,
        ),
        Tool(
            "read_file",
            "Read a local file (text, PDF, image).",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            _rf,
        ),
        Tool(
            "list_files",
            "List files in a directory.",
            {"type": "object", "properties": {"directory": {"type": "string", "default": "."}}},
            _lf,
        ),
        Tool(
            "remember",
            "Save a quick note or fact to the wiki log.",
            {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
            _rem,
        ),
    ]
    return [t for t in _tools if t.name not in (exclude or ())]


BUILTIN_TOOLS = make_tools()
