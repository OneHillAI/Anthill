"""Run a persistent Agent (the third surface) via AgentExecutor - the native execution path.

Mirrors ``scheduler._run_task``'s plane-aware wiring (a Solo agent runs on the local model +
personal context; an Org agent runs on the organization's shared endpoint + org workspace, with
personal context excluded - the privacy invariant), and ADDS the governance the spec requires for
an autonomous worker:

  * a per-action verifier callback - an independent, different-family local model advisory-checks
    each consequential tool action against the mandate (``anthill.verify``, ``kind="action"``); and
  * a human gate - a consequential (approval-flagged) tool is NOT executed headless. It is recorded
    as a pending ``AgentApproval`` and run only once a human approves it.

Imports are lazy inside the functions (matching the scheduler) to avoid import cycles at load.
"""

from __future__ import annotations

import dataclasses
import json

# Tools a "strict" agent routes through the human gate too (writes / external effects), on top of
# any tool that already declares needs_approval (e.g. draft_email).
_STRICT_GATED = {"write_wiki_draft", "create_file", "remember"}


def _identity_name(agent) -> str:
    return f"agent-{agent.id}"


def _principal(db, agent):
    """A least-privilege principal for this agent. Scopes default to its governed identity's; when
    the agent names ``connectors`` (a CSV of tool scopes) the principal is narrowed to those."""
    from ..agent.tools import AgentPrincipal
    from .agents import get_or_create_identity

    ident = get_or_create_identity(db, agent.org_id, _identity_name(agent), agent_type="agent")
    scopes = {s.strip() for s in (ident.scopes or "").split(",") if s.strip()}
    chosen = {s.strip() for s in (agent.connectors or "").split(",") if s.strip()}
    if chosen:
        scopes = (scopes & chosen) or chosen  # narrow to the named connectors
    return AgentPrincipal(name=ident.name, scopes=scopes, active=True)


def _plane_tools(db, agent, plane_inf):
    """Build the plane-scoped tool set (builtin + approved MCP). Under a ``strict`` policy, extra
    write tools are marked as needing approval so they hit the human gate as well."""

    from ..agent.tools import files_owner, make_tools

    # A project agent writes to ITS OWN wiki (team-<id>, when its creator is a member); an Org agent to
    # the org wiki; a Solo agent to the default/personal workspace (#419 P2). One shared resolver.
    from .agent_context import run_wiki_workspace

    ws_path = run_wiki_workspace(
        db,
        plane_inf=plane_inf,
        team_id=getattr(agent, "team_id", None),
        member_user_id=agent.created_by,
        personal_user_id=agent.created_by,
    )
    from .mcp_store import mcp_client_tools

    tools = make_tools(
        workspace=ws_path, owner=files_owner(agent.org_id, agent.created_by)
    ) + mcp_client_tools(db, agent.org_id)  # files scoped per-user (owner=org/user)
    if (agent.governance or "standard") == "strict":
        tools = [
            dataclasses.replace(t, needs_approval=True) if t.name in _STRICT_GATED else t
            for t in tools
        ]
    return tools, ws_path


def _backend_for(agent, plane_inf):
    from ..config import Config
    from ..inference.base import build_backend

    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = agent.model or plane_inf.model  # optional per-agent pin, else the plane default
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key
    return build_backend(config)


def _goal_and_principles(agent, principles: str):
    """Compose the agent's persona into its system principles and its mandate into the goal."""
    persona = (agent.persona or "").strip()
    header = f"You are {agent.name}, an autonomous agent working toward a standing mandate." + (
        f" {persona}" if persona else ""
    )
    full = f"{header}\n\n{principles}".strip() if principles else header
    goal = (agent.mandate or "").strip() or f"Carry out your standing role as {agent.name}."
    return goal, full


def _approval_gate(db, agent, rationale: str):
    """``on_approval_needed``: never auto-run a consequential action headless. Record it as a
    pending ``AgentApproval`` and deny it for this run (the executor reports it as skipped). A human
    approves/rejects it later on the agent's page."""
    from ..agent.tools import tool_scope
    from .db import AgentApproval

    def _gate(tool_name, arguments) -> bool:
        try:
            db.add(
                AgentApproval(
                    org_id=agent.org_id,
                    agent_id=agent.id,
                    tool=tool_name,
                    arguments=json.dumps(arguments, default=str)[:4000],
                    scope=tool_scope(tool_name),
                    rationale=(rationale or "")[:1000],
                    status="pending",
                )
            )
            db.commit()
            # Tell the agent's owner they have something to approve (#284) - the clearest "act now" event.
            if getattr(agent, "created_by", None):
                from .notify import notify

                notify(
                    db,
                    user_id=agent.created_by,
                    org_id=agent.org_id,
                    kind="approval",
                    title=f"{agent.name} needs your approval",
                    body=f"It wants to run {tool_name}. Approve or reject it on the agent's page.",
                    link=f"/agents/{agent.id}",
                )
        except Exception:
            db.rollback()
        return False  # deny -> executor records "(skipped - user did not approve ...)"

    return _gate


def _propose_escalation(
    db, agent, cfg, decrypt, plane_inf, *, goal: str, context: str, answer: str
) -> None:
    """After a run finishes, propose (never silently perform) re-answering on the connected org/RunPod/
    inference-provider backend when this run answered locally and looks uncertain (#278) - the same
    signal Chat's "go deeper" suggestion uses (``intent.looks_uncertain``), applied post-hoc to the
    finished answer rather than as a tool the model must know to call. Recorded as a pending
    ``AgentApproval`` (mirrors ``_approval_gate``'s pattern exactly) - it never blocks or replaces the
    answer already being returned for THIS run; a human approves a follow-up re-run on the agent's page."""
    from ..agent.intent import looks_uncertain
    from ..inference.base import stays_local
    from .db import AgentApproval
    from .plane_routing import org_endpoint_connected

    try:
        if not stays_local(plane_inf.backend, plane_inf.base_url):
            return  # already answered on a remote backend - nothing "stronger" to offer
        if not looks_uncertain(answer):
            return
        if not org_endpoint_connected(cfg, decrypt):
            return
        db.add(
            AgentApproval(
                org_id=agent.org_id,
                agent_id=agent.id,
                tool="escalate_to_provider",
                arguments=json.dumps({"goal": goal, "context": context}, default=str)[:4000],
                scope="model",
                rationale=(
                    "This run's answer looked uncertain and was generated on the local model. "
                    "Approve to re-run it once on the connected org/cloud backend instead."
                )[:1000],
                status="pending",
            )
        )
        db.commit()
        if getattr(agent, "created_by", None):
            from .notify import notify

            notify(
                db,
                user_id=agent.created_by,
                org_id=agent.org_id,
                kind="approval",
                title=f"{agent.name}'s answer looked uncertain",
                body="Approve to re-run it on the connected backend, or leave the local answer as-is.",
                link=f"/agents/{agent.id}",
            )
    except Exception:
        db.rollback()  # a proposal failure must never affect the run whose answer already returned


def _action_verify_cb(url: str, model: str):
    """``on_action_verify``: advisory different-family cross-check of a consequential action that
    actually ran. Returns a Verdict or None; the executor only surfaces it (never blocks)."""

    def _verify(tool_name, arguments, result, *, goal, context):
        try:
            from ..verify import crosscheck_for, verify

            desc = (
                f"Tool: {tool_name}\n"
                f"Arguments: {json.dumps(arguments, default=str)[:800]}\n"
                f"Result: {(result or '')[:800]}"
            )
            return verify(
                desc,
                kind="action",
                context=f"The agent's goal: {goal}\n{context}"[:1000],
                crosscheck=crosscheck_for(url, model),
            )
        except Exception:
            return None

    return _verify


def run_agent(agent, db) -> str:
    """Execute one run of a persistent Agent and return its result text. Plane-aware + governed.

    Solo agents run on the local model with personal memory; Org agents run on the org's shared
    endpoint + org workspace with personal context excluded. Consequential actions are
    verifier-checked and queued for human approval (never fired headless).
    """
    from .. import planes
    from ..agent.executor import AgentExecutor
    from . import audit
    from .agent_context import agent_context_for
    from .agents import audit_hook
    from .crypto import decrypt
    from .db import OrgSettings
    from .plane_routing import plane_inference
    from .recall import recall_memory

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == agent.org_id).first()
    plane = planes.normalize(getattr(agent, "plane", "solo"))
    plane_inf = plane_inference(plane, cfg, decrypt=decrypt)  # raises PlaneUnavailable if org down
    audit.log_inference_call(
        db, plane_inf, org_id=agent.org_id, user_id=agent.created_by, surface="agent"
    )
    backend = _backend_for(agent, plane_inf)
    tools, _ws = _plane_tools(db, agent, plane_inf)

    principles, skills = agent_context_for(
        db,
        user_id=agent.created_by,
        org_id=agent.org_id,
        plane=plane,
        team_id=getattr(agent, "team_id", None),
        is_org=planes.is_org_mode(cfg),
    )
    goal, full_principles = _goal_and_principles(agent, principles)

    url = (getattr(cfg, "ollama_url", "") if cfg else "") or "http://localhost:11434"
    vmodel = (getattr(cfg, "ollama_model", "") or "") if cfg else ""

    executor = AgentExecutor(
        backend,
        tools,
        model=(agent.model or None),
        max_steps=int(getattr(cfg, "agent_max_steps", 12) or 12),  # org run cap (Agent settings)
        identity=_principal(db, agent),
        on_action=audit_hook(db, agent.org_id),
        on_action_verify=_action_verify_cb(url, vmodel),
        on_approval_needed=_approval_gate(db, agent, rationale=goal),
        skills=skills,
        principles=full_principles,
    )
    # Privacy: personal memory is only recalled (and only exists) on the local/personal plane.
    mem_ctx = (
        recall_memory(db, agent.org_id, agent.created_by, goal)
        if (agent.created_by and plane_inf.use_personal_context)
        else ""
    )
    result = executor.run(goal, context=mem_ctx)
    answer = result.answer
    # Council review (Phase 4b): the agent's tool-calling loop already ran exactly once, on `backend`,
    # above - this only ever critiques the finished text via .chat(), never re-runs the loop or gives a
    # reviewer tool access. Degrades to `answer` unchanged on any failure - a review must never break or
    # worsen an agent run. Belt-and-suspenders: review_completed_answer() already degrades internally,
    # this try/except is a second guard in case something outside it (e.g. an import error) raises.
    # Admin-gated (OrgSettings.council_review_tasks, on by default): each review is extra API calls,
    # latency, and cost, and sends the finished answer to additional backends - an org that wants
    # council review for chat but not for task/agent runs can turn just this off.
    if getattr(cfg, "council_review_tasks", True):
        try:
            from ..council.engine import review_completed_answer

            answer = review_completed_answer(cfg, goal, answer, backend, decrypt).answer
        except Exception:
            pass  # keep the pre-review answer; a review failure must never worsen the run
    # #278: propose (never perform) escalating a FOLLOW-UP run to the connected backend when this run
    # answered locally and looks uncertain - judged on the final (post-review) answer, since review may
    # have already resolved the uncertainty. Never affects the answer returned for this run.
    _propose_escalation(
        db, agent, cfg, decrypt, plane_inf, goal=goal, context=mem_ctx, answer=answer
    )
    return answer


def _execute_escalation(db, approval) -> str:
    """Run the human-approved "escalate to the connected backend" action (#278): re-answer the agent's
    goal once on the org/RunPod/inference-provider endpoint, forced via the shared
    ``build_escalation_backend`` (the same plane-forcing mechanism Chat's redo phrase uses) rather than
    the agent's own (possibly local) ``agent.plane``. PII scrubbing is automatic (OpenAICompatBackend,
    same as every other real backend call); raises through to the caller's generic error handling
    (mirrors the plain-tool path in execute_approved) if the endpoint is now disconnected or
    unreachable."""
    from ..agent.executor import AgentExecutor
    from .agent_context import agent_context_for
    from .crypto import decrypt
    from .db import Agent, OrgSettings
    from .escalation import build_escalation_backend

    agent = db.query(Agent).filter(Agent.id == approval.agent_id).first()
    if agent is None:
        return "ERROR: the agent no longer exists"
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == agent.org_id).first()
    backend, _plane_inf = build_escalation_backend(
        db, cfg, decrypt, org_id=agent.org_id, user_id=agent.created_by, surface="agent_approval"
    )
    try:
        parsed = json.loads(approval.arguments or "{}")
    except Exception:
        parsed = {}
    goal = (parsed.get("goal") or "").strip() or (agent.mandate or "").strip()
    context = parsed.get("context") or ""
    principles, _skills = agent_context_for(
        db,
        user_id=agent.created_by,
        org_id=agent.org_id,
        plane="org",
        team_id=getattr(agent, "team_id", None),
        is_org=True,
    )
    _goal_text, full_principles = _goal_and_principles(agent, principles)
    executor = AgentExecutor(
        backend,
        [],  # a one-shot re-answer, not a fresh tool-calling run - matches the approval's own scope
        identity=_principal(db, agent),
        principles=full_principles,
    )
    result = executor.run(goal, context=context)
    return f"[Answered by the connected backend] {result.answer}"


def execute_approved(db, approval) -> str:
    """Execute a single human-approved tool call once, under the agent's plane-scoped tools."""
    from .. import planes
    from . import audit
    from .crypto import decrypt
    from .db import Agent, OrgSettings
    from .plane_routing import plane_inference

    if approval.tool == "escalate_to_provider":
        return _execute_escalation(db, approval)

    agent = db.query(Agent).filter(Agent.id == approval.agent_id).first()
    if agent is None:
        return "ERROR: the agent no longer exists"
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == agent.org_id).first()
    plane_inf = plane_inference(planes.normalize(agent.plane), cfg, decrypt=decrypt)
    audit.log_inference_call(
        db, plane_inf, org_id=agent.org_id, user_id=agent.created_by, surface="agent_approval"
    )
    tools, _ws = _plane_tools(db, agent, plane_inf)
    tool = next((t for t in tools if t.name == approval.tool), None)
    if tool is None:
        return f"ERROR: tool {approval.tool} is not available to this agent"
    try:
        args = json.loads(approval.arguments or "{}")
    except Exception:
        args = {}
    return tool.call(args if isinstance(args, dict) else {})
