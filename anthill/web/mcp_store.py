"""MCP server registry helpers (client side).

Loads agent tools from the org's ADMIN-APPROVED MCP servers and decrypts their stored
auth headers. If the `mcp` SDK is not installed (without the [mcp] extra), this degrades
to no tools and the rest of the app is unaffected.
"""

from __future__ import annotations

import hmac
import json

from .crypto import decrypt
from .db import MCPAccessLog, MCPConsumer, MCPServer, OrgSettings


def decrypt_headers(server) -> dict:
    """The server's stored auth headers ({} if none / undecryptable)."""
    if not server.headers_enc:
        return {}
    try:
        val = json.loads(decrypt(server.headers_enc))
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def decrypt_env(server) -> dict:
    """The server's stored env vars ({} if none) - secrets for a stdio command (e.g. a bot token)."""
    if not getattr(server, "env_enc", ""):
        return {}
    try:
        val = json.loads(decrypt(server.env_enc))
        return {str(k): str(v) for k, v in val.items()} if isinstance(val, dict) else {}
    except Exception:
        return {}


def _decrypt_json(blob: str) -> dict:
    if not blob:
        return {}
    try:
        val = json.loads(decrypt(blob))
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def request_headers(db, server) -> dict:
    """Headers for a request to this server: the static ones plus an OAuth Bearer token when the
    server was connected via OAuth. Refreshes an expired access token (and persists it) before use.
    Falls back to whatever it has if refresh fails - the call will then surface a 401 to Test."""
    headers = decrypt_headers(server)
    tokens = _decrypt_json(getattr(server, "oauth_tokens_enc", "") or "")
    if not tokens.get("access_token"):
        return headers
    from . import mcp_oauth as mo

    if mo.is_expired(tokens) and tokens.get("refresh_token"):
        cfg = _decrypt_json(getattr(server, "oauth_client_enc", "") or "")
        if cfg.get("token_endpoint") and cfg.get("client_id"):
            try:
                import httpx

                with httpx.Client(follow_redirects=True, timeout=25) as c:
                    new = mo.refresh_tokens(
                        c,
                        cfg["token_endpoint"],
                        refresh_token=tokens["refresh_token"],
                        client_id=cfg["client_id"],
                        resource=cfg.get("resource", server.url),
                        client_secret=cfg.get("client_secret", ""),
                    )
                tokens = {
                    "access_token": new["access_token"],
                    "refresh_token": new.get("refresh_token", tokens["refresh_token"]),
                    "expires_at": new["expires_at"],
                }
                from .crypto import encrypt

                server.oauth_tokens_enc = encrypt(json.dumps(tokens))
                db.commit()
            except Exception:
                pass
    headers["Authorization"] = f"Bearer {tokens['access_token']}"
    return headers


def mcp_client_tools(db, org_id) -> list:
    """Tools from the org's APPROVED MCP servers. A pending/disabled server contributes
    nothing; a broken one is skipped (never breaks the toolset)."""
    from ..mcp import mcp_available, mcp_tools_for

    if not mcp_available():
        return []
    tools = []
    rows = (
        db.query(MCPServer).filter(MCPServer.org_id == org_id, MCPServer.status == "approved").all()
    )
    for s in rows:
        try:
            s.mcp_env = decrypt_env(s)  # attach secret env for the stdio launcher
            tools.extend(mcp_tools_for(s, request_headers(db, s)))
        except Exception:
            pass
    return tools


# ── server side: Anthill as MCP server ──────────────────────────────────────────


def consumer_for_token(db, token: str):
    """Return (org_id, MCPConsumer) for the bearer token, else None. Matches a consumer of any
    status (the governance gate decides what a pending/revoked consumer may do). Tokens are stored
    AES-GCM encrypted; we decrypt to compare. Replaces the single shared org token."""
    if not token:
        return None
    for con in db.query(MCPConsumer).all():
        if con.token_enc:
            try:
                if hmac.compare_digest(decrypt(con.token_enc), token):
                    return con.org_id, con
            except Exception:
                pass
    return None


_RESOURCE_EXPOSED = {
    "wiki": "mcp_expose_wiki",
    "cache": "mcp_expose_cache",
    "memory": "mcp_expose_memory",
}


def authorize_query(cfg, consumer, resource) -> tuple[bool, str]:
    """Default-deny gate for one org-brain query. (allowed, reason).

    Denies unless: the MCP server is on, the resource is exposed, the consumer exists and is not
    revoked, the resource is in the consumer's scopes, and the consumer is approved (in `review`
    mode; in `log_only` a pending consumer is answered + audited)."""
    if cfg is None or not getattr(cfg, "mcp_server_enabled", False):
        return False, "mcp server disabled"
    attr = _RESOURCE_EXPOSED.get(resource)
    if attr is None or not getattr(cfg, attr, False):
        return False, f"resource '{resource}' not exposed"
    if consumer is None:
        return False, "unknown consumer"
    if consumer.status == "revoked":
        return False, "consumer revoked"
    scopes = {s.strip() for s in (consumer.scopes or "").split(",") if s.strip()}
    if resource not in scopes:
        return False, f"resource '{resource}' not in consumer scope"
    if consumer.status == "pending" and (getattr(cfg, "mcp_review_mode", "review") or "review") == (
        "review"
    ):
        return False, "consumer pending admin approval"
    return True, "ok"


def serve_org_query(db, org_id, consumer, kind, query, *, client="") -> str:
    """Govern + execute one org-brain query and record it. Logs EVERY attempt (allow or deny, with
    reason) and mirrors to the audit log BEFORE returning - no answer is ever returned without a
    log row. A denied query returns a short notice, never org data."""
    from ..mcp.server import RESOURCES
    from . import audit

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    allowed, reason = authorize_query(cfg, consumer, kind)
    tool = RESOURCES.get(kind, (kind,))[0]
    entry = MCPAccessLog(
        org_id=org_id,
        client=(client or "")[:120],
        consumer_id=getattr(consumer, "id", None),
        tool=tool,
        args_summary=(query or "")[:300],
        query=(query or "")[:1000],
        resources=kind,
        allowed=allowed,
        reason=reason[:120],
    )
    db.add(entry)
    db.commit()
    audit.log(
        db,
        "mcp.query",
        f"consumer={getattr(consumer, 'name', '?')} resource={kind} allowed={allowed} ({reason})",
        org_id=org_id,
    )
    if not allowed:
        return f"(denied: {reason})"
    answer = _answer(db, org_id, kind, query)
    entry.result_summary = (answer or "")[:300]
    db.commit()
    return answer


def _answer(db, org_id, kind, query) -> str:
    """The actual org-brain lookup (separated so tests can stub the model away)."""
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    from ..wiki.workspace import workspace_for

    if kind == "memory":
        from .recall import recall_memory

        return recall_memory(db, org_id, None, query) or "(no relevant memory)"
    if kind == "cache":
        from ..cache import SemanticCache

        hit = SemanticCache(db_path=workspace_for("org").root / ".cache").lookup(query)
        return hit.answer if hit else "(no cached answer)"
    # wiki (default): answer from the org wiki + cache
    from ..config import Config
    from ..inference.base import build_backend
    from ..wiki.ask import ask

    config = Config.from_env()
    if cfg:
        config.model = getattr(cfg, "ollama_model", config.model) or config.model
        config.base_url = getattr(cfg, "ollama_url", config.base_url) or config.base_url
    answer, _slugs, _hit = ask(workspace_for("org"), query, build_backend(config))
    return answer
