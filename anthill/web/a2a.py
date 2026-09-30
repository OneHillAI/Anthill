"""Governed intra-org agent-to-agent (A2A) + task deferral over MCP.

An MCP caller authenticated as an `AgentIdentity` (not just an org) can, when granted the
`a2a` scope, ask or delegate to other agents in the SAME org, or defer a task to the
scheduler. This reuses the existing governance unchanged: scope-gated (`AgentPrincipal.allows`,
via the `a2a_` tool-name prefix), blocked when the calling identity is inactive, attributed,
and audited (`audit.log` + `MCPAccessLog`). **Intra-org only** - every lookup is keyed by the
caller's `org_id`; no token, goal, or task crosses an org boundary. (See the design doc
`anthill-internal/engineering-plans/MCP_A2A_DESIGN.md`.)
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone

from . import audit
from .crypto import decrypt, encrypt
from .db import AgentIdentity, MCPAccessLog, OrgSettings, ScheduledTask

A2A_SCOPE = "a2a"


def mint_identity_token(db, ident: AgentIdentity) -> str:
    """Generate + store (AES-GCM encrypted) an MCP bearer token for an agent identity.
    Returns the plaintext token once (it is not retrievable afterwards)."""
    token = "anthill_a2a_" + secrets.token_urlsafe(32)
    ident.mcp_token_enc = encrypt(token)  # type: ignore[assignment]  # SQLAlchemy Column
    db.commit()
    return token


def identity_for_mcp_token(db, token: str):
    """(org_id, AgentIdentity) for the identity whose token matches, else None.
    Tokens are stored encrypted; we decrypt to compare."""
    if not token:
        return None
    for ident in db.query(AgentIdentity).filter(AgentIdentity.mcp_token_enc != "").all():
        try:
            if hmac.compare_digest(decrypt(ident.mcp_token_enc), token):
                return ident.org_id, ident
        except Exception:
            pass
    return None


def _log(db, org_id, caller_name, tool, *, allowed, client="") -> None:
    """Record an A2A call in the access log + audit trail (best-effort)."""
    try:
        db.add(
            MCPAccessLog(
                org_id=org_id,
                client=(client or "")[:120],
                tool=tool,
                args_summary=f"caller={caller_name} allowed={allowed}"[:300],
            )
        )
        audit.log(
            db,
            "a2a.call" if allowed else "a2a.blocked",
            f"caller={caller_name} tool={tool}",
            org_id=org_id,
        )
        db.commit()
    except Exception:
        pass


def serve_a2a(db, org_id: int, caller, name: str, arguments: dict, *, client="") -> str:
    """Dispatch one A2A / deferral tool call from `caller` (an AgentPrincipal).

    Governance is the same as in-process: `caller.allows(name)` (active + `a2a` scope) or it
    is refused. Raises PermissionError when not allowed. Intra-org only.
    """
    if not caller.allows(name):  # tool_scope("a2a_*") == "a2a"
        _log(db, org_id, caller.name, name, allowed=False, client=client)
        raise PermissionError(f"{caller.name} lacks the '{A2A_SCOPE}' scope or is inactive")
    _log(db, org_id, caller.name, name, allowed=True, client=client)

    if name == "a2a_defer_task":
        return _defer_task(db, org_id, arguments)

    target_name = str(arguments.get("agent", "")).strip()
    prompt = str(arguments.get("question") or arguments.get("goal") or "").strip()
    if not target_name or not prompt:
        raise ValueError("both 'agent' and 'question'/'goal' are required")
    return _run_target_agent(db, org_id, target_name, prompt)


def _defer_task(db, org_id: int, arguments: dict) -> str:
    """Queue a ScheduledTask for the scheduler to run later (under its governed identity)."""
    goal = str(arguments.get("goal", "")).strip()
    if not goal:
        raise ValueError("'goal' is required")
    from .scheduler import _normalize_task_schedule

    schedule = _normalize_task_schedule(str(arguments.get("schedule", "once")))
    if not schedule:
        raise ValueError("invalid task schedule")
    task = ScheduledTask(
        org_id=org_id,
        created_by=None,  # queued by an agent identity, not a user
        title=f"[a2a] {goal[:60]}",
        goal=goal,
        schedule=schedule,
        status="pending",
        next_run_at=datetime.now(timezone.utc),  # eligible on the next tick
    )
    db.add(task)
    db.flush()
    from . import task_occurrences

    task_occurrences.create_initial(db, task, getattr(task, "next_run_at", None))
    db.commit()
    return f"Task #{task.id} queued ({schedule})."


def _run_target_agent(db, org_id: int, target_name: str, prompt: str) -> str:
    """Run another org agent under ITS identity/scopes and return its answer. The target
    is bounded by its own scopes, so delegation can never escalate privilege. The target's
    toolset is builtins + approved MCP servers (NOT the a2a tools), so there is no
    automatic a2a recursion - delegation depth is naturally 1."""
    import os

    target = (
        db.query(AgentIdentity)
        .filter(AgentIdentity.org_id == org_id, AgentIdentity.name == target_name)
        .first()
    )
    if not target:
        raise ValueError(f"no agent named '{target_name}' in this organization")
    if not target.active:
        raise PermissionError(f"agent '{target_name}' is inactive")

    from ..agent.executor import AgentExecutor
    from ..agent.tools import files_owner, make_tools
    from ..config import Config
    from ..inference.base import build_backend
    from .agent_context import agent_context_for
    from .agents import audit_hook, principal_for
    from .mcp_store import mcp_client_tools

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    config = Config.from_env()
    if cfg:
        config.model = getattr(cfg, "ollama_model", config.model) or config.model
        config.base_url = getattr(cfg, "ollama_url", config.base_url) or config.base_url

    ws_path = os.environ.get("ANTHILL_WORKSPACE", "workspace")
    # a2a is agent-to-agent (no single end-user), so files stay org-scoped here.
    tools = make_tools(workspace=ws_path, owner=files_owner(org_id)) + mcp_client_tools(db, org_id)
    # A2A serves the org brain to another agent: org scope only (never personal).
    principles, skills = agent_context_for(
        db, user_id=None, org_id=org_id, plane="org", is_org=True
    )
    executor = AgentExecutor(
        build_backend(config),
        tools,
        max_steps=8,
        identity=principal_for(target),
        on_action=audit_hook(db, org_id),
        skills=skills,
        principles=principles,
    )
    return executor.run(prompt).answer
