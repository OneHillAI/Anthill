"""Agent-identity helpers: get-or-create governed identities, build the
runtime principal, and an audit hook that attributes every tool call to it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..agent.tools import DEFAULT_AGENT_SCOPES, AgentPrincipal
from . import audit
from .db import AgentIdentity


def get_or_create_identity(
    db: Session, org_id: int, name: str, agent_type: str = "custom"
) -> AgentIdentity:
    ident = (
        db.query(AgentIdentity)
        .filter(AgentIdentity.org_id == org_id, AgentIdentity.name == name)
        .first()
    )
    if ident is None:
        ident = AgentIdentity(
            org_id=org_id,
            name=name,
            agent_type=agent_type,
            scopes=",".join(sorted(DEFAULT_AGENT_SCOPES)),
            active=True,
        )
        db.add(ident)
        db.commit()
    return ident


def principal_for(ident: AgentIdentity) -> AgentPrincipal:
    scopes = {s.strip() for s in (ident.scopes or "").split(",") if s.strip()}
    return AgentPrincipal(name=ident.name, scopes=scopes, active=bool(ident.active))


def audit_hook(db: Session, org_id: int | None):
    """Return an on_action(identity, tool, scope, allowed) callback for the executor."""

    def _hook(identity_name: str, tool: str, scope: str, allowed: bool) -> None:
        try:
            audit.log(
                db,
                "agent.tool" if allowed else "agent.blocked",
                f"identity={identity_name} tool={tool} scope={scope} allowed={allowed}",
                org_id=org_id,
            )
        except Exception:
            pass  # auditing must never break the run

    return _hook
