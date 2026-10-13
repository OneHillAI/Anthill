"""The single assembler for an agent's always-on context: guiding principles +
the skills of every scope the user is in.

Keyed by SCOPE, not identity name - so every agent (chat-agent, scheduler, any future
custom AgentIdentity) and the non-agent ask() path inherit the same principles and the
in-scope skills automatically. The identity's capability scopes still gate which skills
may be *acted on* (enforced in the executor).
"""

from __future__ import annotations


def is_active_member(db, user_id, team_id) -> bool:
    """True iff the user is an ACTIVE member of the team. The trust boundary for project wiki routing:
    a supplied ``team_id`` may only scope a run to a project the user actually belongs to, so a
    manipulated id can never reach another project's wiki (#419 P2 / #467 review)."""
    from .db import TeamMembership

    if not (user_id and team_id):
        return False
    return (
        db.query(TeamMembership)
        .filter(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
            TeamMembership.status == "active",
        )
        .first()
        is not None
    )


def project_connects_parent(db, team_id) -> bool:
    """Whether a project connects to (also reads) its PARENT wiki - the org wiki for an org project, the
    personal wiki for a Solo/local project (#419). Default True (connected) for any missing/unknown id,
    so behaviour is unchanged unless a project explicitly disconnects. Read-grounding only."""
    from .db import Team

    if not team_id:
        return True
    row = db.query(Team.connect_parent_wiki).filter(Team.id == team_id).first()
    # NULL-safe: a missing project, or a legacy row not yet backfilled, reads as connected (the default).
    # (ensure_columns adds the column with DEFAULT 1, so existing projects migrate to connected anyway.)
    return True if (row is None or row[0] is None) else bool(row[0])


def _org_for(db, user_id, org_id=None):
    """The organisation whose org wiki a run reads: the one the caller names, else the organisation of the
    user the run is for. None when there is neither; ``workspace_for("org")`` then raises, so a run with no
    user must name its organisation."""
    if org_id is not None:
        return org_id
    if not user_id:
        return None
    from .db import User

    row = db.query(User.org_id).filter(User.id == int(user_id)).first()
    return row[0] if row else None


def project_read_extras(db, *, team_id, use_personal_context, wiki_scope, user_id, org_id=None):
    """The read-only PARENT workspaces to blend into a project chat's grounding (in addition to the
    project's own wiki, which is the base): the org wiki (org project, ``use_personal_context`` False) or
    the user's personal wiki (Solo/local project) - and only when the project CONNECTS its parent and the
    ``wiki_scope`` allows it. Never another project. This is the chat read-side mirror of the parent blend
    in ``context_workspaces``, using the same ``project_connects_parent`` predicate so they cannot drift."""
    from ..wiki.workspace import workspace_for

    if not project_connects_parent(db, team_id):
        return []
    if not use_personal_context and wiki_scope in ("all", "org", "team"):
        return [workspace_for("org", org_id=_org_for(db, user_id, org_id))]
    if use_personal_context and wiki_scope in ("all", "personal"):
        return [workspace_for("personal", user_id=user_id)]
    return []


def run_wiki_workspace(
    db, *, plane_inf, team_id, member_user_id, personal_user_id=None, org_id=None
) -> str:
    """The ONE resolver for a run's wiki workspace (read+write base) - chat, scheduled tasks, and agents
    all call it so they can never drift on where a run's wiki lives or on the trust boundary. Returns the
    workspace path:

      - a project's own wiki (``team-<id>``) WHEN the run is in a project AND ``member_user_id`` is an
        active member of it (a supplied/stale ``team_id`` can never reach a project the user is not in);
      - else the shared **org** wiki (cloud runs); else the **personal**/default workspace (Solo/local).

    ``personal_user_id`` selects the per-user personal wiki (``user-<id>``, the chat + agent-context read
    side) vs the single-node ``ANTHILL_WORKSPACE`` (scheduler/agents back-compat) when omitted.
    """
    import os

    from ..wiki.workspace import workspace_for

    if (
        getattr(plane_inf, "wiki_scope", "") == "team"
        and team_id
        and is_active_member(db, member_user_id, team_id)
    ):
        return str(workspace_for("team", team_id=team_id).root)
    # The shared org wiki ONLY for a connected, non-personal (cloud) run. A missing/unavailable
    # plane_inf (e.g. PlaneUnavailable) falls back to personal/default - never route a degraded run to
    # the shared org wiki for reads or writes (matches the prior chat behaviour).
    if plane_inf is not None and not getattr(plane_inf, "use_personal_context", True):
        return str(
            workspace_for(
                "org", org_id=_org_for(db, member_user_id or personal_user_id, org_id)
            ).root
        )
    if personal_user_id:
        return str(workspace_for("personal", user_id=personal_user_id).root)
    return os.environ.get("ANTHILL_WORKSPACE", "workspace")


def scoped_workspaces(db, *, user_id, org_id):
    """[(tier, label, workspace)] in precedence order: org -> the user's teams -> personal.
    With no user (system runs), it's org + the single-node personal workspace."""
    from ..wiki.workspace import workspace_for
    from .db import TeamMembership

    scoped = [("org", "Organization", workspace_for("org", org_id=org_id))]
    if user_id:
        team_ids = [
            t
            for (t,) in db.query(TeamMembership.team_id)
            .filter(TeamMembership.user_id == user_id, TeamMembership.status == "active")
            .all()
        ]
        for t in team_ids:
            scoped.append(("team", f"Team {t}", workspace_for("team", team_id=t)))
        scoped.append(("personal", "Personal", workspace_for("personal", user_id=user_id)))
    else:
        scoped.append(("personal", "Personal", workspace_for("personal")))
    return scoped


def context_workspaces(db, *, user_id, plane="solo", team_id=None, is_org=False, org_id=None):
    """The workspaces a chat/task in ``plane`` should see for principles + skills. This MIRRORS the
    chat read-side wiki grounding (app.py): the org wiki (when this install is an org), the user's
    team wikis (their projects, always available), and the personal wiki ONLY on local compute - so
    personal context never reaches the org cloud (the privacy invariant + the principles/skills
    leak fix). Returns [(tier, label, workspace)] broad -> specific:

      Solo  (local)      -> org (if org) + your teams + personal.
      Org   (cloud)      -> org + your teams; NO personal.
      In a PROJECT (plane=team + team_id) -> org (read-only, if org) + THAT project's wiki only
                            (+ personal when local) - not every team the user is in (#419 P2 / Gap A).

    When ``team_id`` is set on a team-plane run, the run is scoped to that ONE project's wiki, so
    "the wiki is used only for the project" holds at runtime; otherwise (solo/org, or a team run with
    no id) the broad "all your teams" grounding is kept. ``scoped_workspaces`` stays the unscoped layer
    for the wiki "Active for you" view.
    """
    from .. import planes
    from ..wiki.workspace import workspace_for
    from .db import TeamMembership

    p = planes.normalize(plane)
    personal_ok = p == "solo" or (p == "team" and not is_org)  # == plane_inf.use_personal_context
    in_project = bool(
        user_id and p == "team" and team_id and is_active_member(db, user_id, team_id)
    )
    # A project reads its parent wiki (org, or personal when local) only when it connects it at setup; a
    # disconnected project is isolated (its own wiki only). Non-project runs always keep their grounding.
    connects_parent = project_connects_parent(db, team_id) if in_project else True
    scoped = []
    if is_org and connects_parent:
        scoped.append(
            ("org", "Organization", workspace_for("org", org_id=_org_for(db, user_id, org_id)))
        )
    if in_project:
        # A project run grounds in ITS OWN wiki only (the user is an active member).
        scoped.append(("team", f"Team {team_id}", workspace_for("team", team_id=team_id)))
    elif user_id:
        # Non-project runs (solo/org) AND a team run for a project the user is not (or no longer) an
        # active member of both fall back to the broad "all your active teams" grounding (spec R3): a
        # supplied/stale team_id never reaches a foreign project, and the user still sees their own.
        team_ids = [
            t
            for (t,) in db.query(TeamMembership.team_id)
            .filter(TeamMembership.user_id == user_id, TeamMembership.status == "active")
            .all()
        ]
        for t in team_ids:
            scoped.append(("team", f"Team {t}", workspace_for("team", team_id=t)))
    if personal_ok and user_id and connects_parent:
        scoped.append(("personal", "Personal", workspace_for("personal", user_id=user_id)))
    return scoped


def agent_context_for(db, *, user_id, org_id, plane="solo", team_id=None, is_org=False):
    """(principles, skills) scoped to this run's plane (see ``context_workspaces``). Defaults to the
    Solo scope (personal only) so a caller that does not pass a plane can never over-share."""
    from ..agent.skills import load_skills
    from ..wiki.principles import assemble_principles

    scoped = context_workspaces(
        db, user_id=user_id, plane=plane, team_id=team_id, is_org=is_org, org_id=org_id
    )
    principles = assemble_principles([(label, ws) for _tier, label, ws in scoped])
    skills = load_skills(scoped=[(tier, ws) for tier, _label, ws in scoped])
    return principles, skills
