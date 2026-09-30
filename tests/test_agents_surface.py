"""The Agents surface (third category): the Agent object + its governed run.

Covers the P0 guarantees: an agent is created and scoped per plane (a solo agent is private to its
creator), a consequential action is NOT run headless but queued for human approval (the gate), and
the run wires the per-action verifier + approval gate + the agent's persona/mandate. Model-free.
"""

import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    from fk_seed import seed_org_and_users

    db = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    seed_org_and_users(db)
    db.commit()
    return db


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    a = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    b = User(org_id=org.id, email="b@acme.com", role="member", active=True)
    s.add_all([a, b])
    s.commit()
    return TestClient(app_mod.app), {"org": org.id, "a": a.id, "b": b.id}


def _auth(client, uid, org_id, role):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


# ── the surface renders and creates ────────────────────────────────────────────


def test_create_lists_and_details_an_agent(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    r = client.post(
        "/agents/create",
        data={
            "name": "Research Scout",
            "persona": "A meticulous scout.",
            "mandate": "Keep the wiki current on competitors.",
            "plane": "solo",
            "schedule": "manual",
            "governance": "standard",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"].startswith("/agents/")
    lst = client.get("/agents")
    assert "Research Scout" in lst.text and "plane-dot-solo" in lst.text
    detail = client.get(r.headers["location"])
    assert "Research Scout" in detail.text and "has not run yet" in detail.text


def test_agent_create_form_is_a_modal(tmp_path, monkeypatch):
    # The "+ New agent" create form is presented as a modal dialog (overlay + card + close), not an
    # inline card. The reveal still just flips the element's display, so the existing JS is unchanged.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    body = client.get("/agents").text
    assert 'id="new-agent" class="modal"' in body  # the create form is a modal overlay...
    assert (
        'class="modal-card"' in body and 'class="modal-x"' in body
    )  # ...with a card + close button
    assert "Create an agent" in body  # the form itself is intact


def test_nav_has_agents_and_agent_access_via_the_org_hub(tmp_path, monkeypatch):
    # the new worker surface stays on the rail; agent-access (identity governance) moved into the Org
    # settings hub in the P1 consolidation (#462), so it is reached from there, not a rail item.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    assert 'href="/agents"' in client.get("/agents").text  # the Agents surface is on the rail
    assert 'href="/agent-access"' in client.get("/settings/org").text  # in the Org settings hub now
    assert client.get("/agent-access").status_code == 200  # still works


# ── privacy: a solo agent is private to its creator ────────────────────────────


def test_solo_agent_is_private_to_its_creator(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    loc = client.post(
        "/agents/create",
        data={"name": "A-Private", "mandate": "do a thing", "plane": "solo", "schedule": "manual"},
        follow_redirects=False,
    ).headers["location"]
    # user B must not see A's solo agent - not in the list, and the detail redirects away
    _auth(client, ids["b"], ids["org"], "member")
    assert "A-Private" not in client.get("/agents").text
    assert client.get(loc, follow_redirects=False).status_code == 302


# ── the human gate: a consequential action is queued, not run headless ─────────


def test_approval_gate_defers_a_consequential_action(tmp_path, monkeypatch):
    from anthill.web import agents_run
    from anthill.web.db import Agent, AgentApproval

    db = _session(tmp_path, monkeypatch)
    agent = Agent(org_id=1, created_by=1, name="Gatekeeper", mandate="m", plane="solo")
    db.add(agent)
    db.commit()
    gate = agents_run._approval_gate(db, agent, rationale="send the weekly update")
    ran = gate("draft_email", {"to": "boss@x.com", "body": "hi"})
    assert ran is False  # the executor sees "not approved" -> the tool never runs headless
    row = db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).one()
    assert row.status == "pending" and row.tool == "draft_email" and "boss@x.com" in row.arguments


def test_strict_governance_gates_write_tools(tmp_path, monkeypatch):
    from anthill.agent.tools import Tool
    from anthill.web import agents_run
    from anthill.web.db import Agent

    db = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agents_run,
        "_STRICT_GATED",
        {"create_file"},
        raising=True,
    )
    made = [
        Tool("web_search", "", {}, fn=lambda **k: "", needs_approval=False),
        Tool("create_file", "", {}, fn=lambda **k: "", needs_approval=False),
    ]
    monkeypatch.setattr("anthill.agent.tools.make_tools", lambda **k: made)
    monkeypatch.setattr("anthill.web.mcp_store.mcp_client_tools", lambda db, org_id: [])
    plane_inf = types.SimpleNamespace(use_personal_context=True)

    strict = Agent(org_id=1, name="S", mandate="m", plane="solo", governance="strict")
    tools, _ = agents_run._plane_tools(db, strict, plane_inf)
    by = {t.name: t for t in tools}
    assert by["create_file"].needs_approval is True  # a write is gated under strict
    assert by["web_search"].needs_approval is False  # a read is not


# ── the run wires governance (verifier + gate) + the persona/mandate ───────────


def test_run_agent_wires_verifier_gate_and_persona(tmp_path, monkeypatch):
    from anthill.web import agents_run
    from anthill.web.db import Agent

    db = _session(tmp_path, monkeypatch)
    captured = {}

    class _SpyExecutor:
        def __init__(self, backend, tools, **kw):
            captured.update(kw)
            captured["n_tools"] = len(tools)

        def run(self, goal, *, context=""):
            captured["goal"] = goal
            captured["context"] = context
            return types.SimpleNamespace(answer="ok")

    # Only the executor is stubbed; plane routing / tools / context run for real (cheap, model-free).
    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _SpyExecutor)

    agent = Agent(
        org_id=1,
        created_by=1,
        name="Scout",
        persona="You cite sources.",
        mandate="Summarise competitor news.",
        plane="solo",
        governance="standard",
    )
    db.add(agent)
    db.commit()
    answer = agents_run.run_agent(agent, db)

    assert answer == "ok"
    assert captured["on_action_verify"] is not None  # consequential actions are verifier-checked
    assert captured["on_approval_needed"] is not None  # ... and human-gateable
    assert captured["identity"].name == f"agent-{agent.id}"  # its own governed identity
    assert "You cite sources." in captured["principles"]  # persona shapes the system prompt
    assert captured["goal"] == "Summarise competitor news."  # the mandate is the goal


# ── council review gate (Phase 4b): OrgSettings.council_review_tasks ────────────


def _agent_run_with_council_gate(tmp_path, monkeypatch, *, council_review_tasks):
    """Run one agent through the real run_agent(), stubbing only the executor and the council review
    call itself (so this proves the ORG-LEVEL GATE, not review_completed_answer's own internals -
    those are covered by tests/test_council_review.py)."""
    from anthill.web import agents_run
    from anthill.web.db import Agent, OrgSettings

    db = _session(tmp_path, monkeypatch)
    db.add(OrgSettings(org_id=1, council_review_tasks=council_review_tasks))
    db.commit()

    class _StubExecutor:
        def __init__(self, backend, tools, **kw):
            pass

        def run(self, goal, *, context=""):
            return types.SimpleNamespace(answer="the draft answer")

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _StubExecutor)

    review_calls = []
    monkeypatch.setattr(
        "anthill.council.engine.review_completed_answer",
        lambda *a, **k: (
            review_calls.append((a, k)) or types.SimpleNamespace(answer="reviewed answer")
        ),
    )

    agent = Agent(
        org_id=1,
        created_by=1,
        name="Scout",
        mandate="do the thing",
        plane="solo",
        governance="standard",
    )
    db.add(agent)
    db.commit()
    answer = agents_run.run_agent(agent, db)
    return answer, review_calls


def test_council_review_gate_on_by_default_invokes_review(tmp_path, monkeypatch):
    answer, review_calls = _agent_run_with_council_gate(
        tmp_path, monkeypatch, council_review_tasks=True
    )
    assert len(review_calls) == 1
    assert answer == "reviewed answer"


def test_council_review_gate_off_never_invokes_review(tmp_path, monkeypatch):
    answer, review_calls = _agent_run_with_council_gate(
        tmp_path, monkeypatch, council_review_tasks=False
    )
    assert review_calls == []
    assert answer == "the draft answer"  # the executor's own result, untouched


# ── #278: propose (never perform) escalating an uncertain local answer ─────────


def _solo_topology_connected_cfg(**overrides):
    from anthill.web.db import OrgSettings

    kw = {
        "org_id": 1,
        "deployment_topology": "solo",
        "org_backend_status": "validated",
        "org_model_endpoint": "https://gpu.acme.example/v1",
        "org_model": "qwen2.5:32b",
        "solo_compute": "local",
    }
    kw.update(overrides)
    return OrgSettings(**kw)


def test_propose_escalation_creates_a_pending_approval_when_eligible(tmp_path, monkeypatch):
    from anthill.web.agents_run import _propose_escalation
    from anthill.web.crypto import decrypt as real_decrypt
    from anthill.web.db import Agent, AgentApproval
    from anthill.web.plane_routing import PlaneInference

    db = _session(tmp_path, monkeypatch)
    db.add(_solo_topology_connected_cfg())
    db.commit()
    from anthill.web.db import OrgSettings

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.commit()

    local_plane = PlaneInference(
        plane="solo",
        backend="ollama",
        base_url="http://localhost:11434",
        model="qwen2.5:3b",
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )
    uncertain = "I'm not entirely sure, but the answer is probably 42."
    _propose_escalation(
        db,
        agent,
        cfg,
        real_decrypt,
        local_plane,
        goal="do the thing",
        context="ctx",
        answer=uncertain,
    )
    row = db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).one()
    assert row.status == "pending" and row.tool == "escalate_to_provider"
    assert "do the thing" in row.arguments


def test_propose_escalation_skips_a_confident_answer(tmp_path, monkeypatch):
    from anthill.web.agents_run import _propose_escalation
    from anthill.web.crypto import decrypt as real_decrypt
    from anthill.web.db import Agent, AgentApproval, OrgSettings
    from anthill.web.plane_routing import PlaneInference

    db = _session(tmp_path, monkeypatch)
    db.add(_solo_topology_connected_cfg())
    db.commit()
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.commit()

    local_plane = PlaneInference(
        plane="solo",
        backend="ollama",
        base_url="http://localhost:11434",
        model="qwen2.5:3b",
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )
    _propose_escalation(
        db, agent, cfg, real_decrypt, local_plane, goal="g", context="c", answer="The answer is 42."
    )
    assert db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).count() == 0


def test_propose_escalation_skips_when_already_answered_remotely(tmp_path, monkeypatch):
    from anthill.web.agents_run import _propose_escalation
    from anthill.web.crypto import decrypt as real_decrypt
    from anthill.web.db import Agent, AgentApproval, OrgSettings
    from anthill.web.plane_routing import PlaneInference

    db = _session(tmp_path, monkeypatch)
    db.add(_solo_topology_connected_cfg())
    db.commit()
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.commit()

    org_plane = PlaneInference(
        plane="solo",
        backend="openai",
        base_url="https://gpu.acme.example/v1",
        model="qwen2.5:32b",
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
        ephemeral=True,
    )
    uncertain = "I'm not entirely sure, but the answer is probably 42."
    _propose_escalation(
        db, agent, cfg, real_decrypt, org_plane, goal="g", context="c", answer=uncertain
    )
    assert db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).count() == 0


def test_propose_escalation_skips_when_nothing_connected(tmp_path, monkeypatch):
    from anthill.web.agents_run import _propose_escalation
    from anthill.web.crypto import decrypt as real_decrypt
    from anthill.web.db import Agent, AgentApproval, OrgSettings
    from anthill.web.plane_routing import PlaneInference

    db = _session(tmp_path, monkeypatch)
    db.add(OrgSettings(org_id=1))  # no backend connected
    db.commit()
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.commit()

    local_plane = PlaneInference(
        plane="solo",
        backend="ollama",
        base_url="http://localhost:11434",
        model="qwen2.5:3b",
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )
    uncertain = "I'm not entirely sure, but the answer is probably 42."
    _propose_escalation(
        db, agent, cfg, real_decrypt, local_plane, goal="g", context="c", answer=uncertain
    )
    assert db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).count() == 0


def test_run_agent_proposes_escalation_for_an_uncertain_local_answer(tmp_path, monkeypatch):
    # End-to-end through the real run_agent(): a solo-topology account with a connected endpoint stays
    # on local by default (Decision 0), and an uncertain answer proposes a follow-up escalation.
    from anthill.web import agents_run
    from anthill.web.db import Agent, AgentApproval

    db = _session(tmp_path, monkeypatch)
    db.add(_solo_topology_connected_cfg())
    db.commit()

    class _StubExecutor:
        def __init__(self, backend, tools, **kw):
            pass

        def run(self, goal, *, context=""):
            return types.SimpleNamespace(answer="I'm not entirely sure, but it's probably 42.")

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _StubExecutor)
    monkeypatch.setattr(
        "anthill.council.engine.review_completed_answer",
        lambda cfg, goal, answer, backend, decrypt: types.SimpleNamespace(answer=answer),
    )

    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="do the thing", plane="solo")
    db.add(agent)
    db.commit()
    answer = agents_run.run_agent(agent, db)

    assert "not entirely sure" in answer  # the run's own answer is returned unchanged
    row = db.query(AgentApproval).filter(AgentApproval.agent_id == agent.id).one()
    assert row.tool == "escalate_to_provider" and row.status == "pending"


def test_execute_approved_escalation_reruns_on_the_connected_backend(tmp_path, monkeypatch):
    from anthill.web.agents_run import execute_approved
    from anthill.web.db import Agent, AgentApproval

    db = _session(tmp_path, monkeypatch)
    db.add(_solo_topology_connected_cfg())
    db.commit()

    class _StubExecutor:
        def __init__(self, backend, tools, **kw):
            self._backend = backend

        def run(self, goal, *, context=""):
            return types.SimpleNamespace(answer=f"stronger answer to: {goal}")

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _StubExecutor)

    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.flush()
    approval = AgentApproval(
        org_id=1,
        agent_id=agent.id,
        tool="escalate_to_provider",
        arguments='{"goal": "do the thing", "context": "ctx"}',
        scope="model",
        status="pending",
    )
    db.add(approval)
    db.commit()

    result = execute_approved(db, approval)
    assert "stronger answer to: do the thing" in result
    assert "[Answered by the connected backend]" in result


def test_execute_approved_escalation_errors_when_disconnected(tmp_path, monkeypatch):
    # If the endpoint is disconnected between the proposal and the approval, execute_approved's
    # generic try/except (app.py's agent_approval_approve) turns the raised PlaneUnavailable into an
    # "ERROR: ..." result - proven here at the function level, matching how a plain tool's failure
    # would surface.
    from anthill.web.agents_run import _execute_escalation
    from anthill.web.db import Agent, AgentApproval, OrgSettings
    from anthill.web.plane_routing import PlaneUnavailable

    db = _session(tmp_path, monkeypatch)
    db.add(OrgSettings(org_id=1))  # nothing connected
    db.commit()
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.flush()
    approval = AgentApproval(
        org_id=1,
        agent_id=agent.id,
        tool="escalate_to_provider",
        arguments='{"goal": "do the thing", "context": ""}',
        scope="model",
        status="pending",
    )
    db.add(approval)
    db.commit()

    try:
        _execute_escalation(db, approval)
        raise AssertionError("expected PlaneUnavailable")
    except PlaneUnavailable:
        pass


# ── run history: every run is recorded and shown (not just the latest) ──────────


def _seed_due_agent(tmp_path, monkeypatch, **kw):
    """An active agent due to run now, on its own engine (the tick needs the engine)."""
    from datetime import datetime, timezone

    from anthill.web.db import Agent

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    from fk_seed import seed_org_and_users

    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = Session()
    seed_org_and_users(s)
    s.commit()
    base = {
        "org_id": 1,
        "created_by": 1,
        "name": "Hist",
        "mandate": "m",
        "plane": "solo",
        "schedule": "manual",
        "status": "active",
        "next_run_at": datetime.now(timezone.utc),
    }
    base.update(kw)
    agent = Agent(**base)
    s.add(agent)
    s.commit()
    aid = agent.id
    # Isolate the AgentRun recording: stub the run + the best-effort verify/remember (model-free).
    from anthill.web import scheduler

    monkeypatch.setattr(scheduler, "_verify_agent_result", lambda a, r, db: None)
    monkeypatch.setattr(scheduler, "_remember_agent_outcome", lambda a, r, db: None)
    return eng, Session, aid


def test_agent_tick_records_a_run_in_history(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import AgentRun

    eng, Session, aid = _seed_due_agent(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.web.agents_run.run_agent", lambda a, db: "did the thing")
    scheduler._agent_tick(eng)

    runs = Session().query(AgentRun).filter(AgentRun.agent_id == aid).all()
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "ok" and r.result == "did the thing"
    assert r.trigger == "manual"  # a manual-schedule agent's runs are manual
    assert r.finished_at is not None and r.duration_ms is not None


def test_agent_tick_records_an_error_run(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import AgentRun

    eng, Session, aid = _seed_due_agent(tmp_path, monkeypatch)

    def _boom(a, db):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("anthill.web.agents_run.run_agent", _boom)
    scheduler._agent_tick(eng)

    r = Session().query(AgentRun).filter(AgentRun.agent_id == aid).one()
    assert r.status == "error" and "kaboom" in r.error and r.result == ""
    # a failed run leaves no verifier verdict
    assert r.verify_needs_review is False


def test_agent_detail_renders_run_history(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from anthill.web.db import Agent, AgentRun

    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    loc = client.post(
        "/agents/create",
        data={"name": "Histbot", "mandate": "m", "plane": "solo", "schedule": "manual"},
        follow_redirects=False,
    ).headers["location"]
    aid = int(loc.rstrip("/").split("/")[-1])

    import anthill.web.app as app_mod

    s = app_mod._SessionFactory()
    agent = s.query(Agent).filter(Agent.id == aid).first()
    agent.run_count = 1
    s.add(
        AgentRun(
            org_id=ids["org"],
            agent_id=aid,
            trigger="manual",
            status="ok",
            result="a distinctive result line",
            finished_at=datetime.now(timezone.utc),
        )
    )
    s.commit()

    detail = client.get(loc).text
    assert "Run history" in detail and "a distinctive result line" in detail


# ── settings: org defaults + run cap + per-agent tool scopes ────────────────────


def _cfg_of(org_id):
    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    return app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


def _agent_of(aid):
    import anthill.web.app as app_mod
    from anthill.web.db import Agent

    return app_mod._SessionFactory().query(Agent).filter(Agent.id == aid).first()


def _make(client, **data):
    return int(
        client.post("/agents/create", data=data, follow_redirects=False)
        .headers["location"]
        .rstrip("/")
        .split("/")[-1]
    )


def test_agents_settings_save_and_clamp(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    client.post(
        "/agents/settings",
        data={
            "agent_default_governance": "strict",
            "agent_default_model": "qwen3:14b",
            "agent_max_steps": "7",
        },
        follow_redirects=False,
    )
    cfg = _cfg_of(ids["org"])
    assert cfg.agent_default_governance == "strict" and cfg.agent_default_model == "qwen3:14b"
    assert cfg.agent_max_steps == 7
    # the cost-rail cap is clamped
    client.post("/agents/settings", data={"agent_max_steps": "999"}, follow_redirects=False)
    assert _cfg_of(ids["org"]).agent_max_steps == 50


def test_agent_defaults_card_is_admin_only(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    assert "Agent defaults" in client.get("/agents").text
    _auth(client, ids["b"], ids["org"], "member")
    assert "Agent defaults" not in client.get("/agents").text


def test_create_saves_validated_tool_scopes(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(
        client,
        name="Scoped",
        mandate="m",
        plane="solo",
        schedule="manual",
        connectors=["web", "email", "bogus"],  # bogus is dropped
    )
    assert _agent_of(aid).connectors == "web,email"


def test_create_falls_back_to_org_default_model(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    client.post(
        "/agents/settings", data={"agent_default_model": "qwen3:14b"}, follow_redirects=False
    )
    aid = _make(client, name="NoModel", mandate="m", plane="solo", schedule="manual")
    assert _agent_of(aid).model == "qwen3:14b"  # blank model fell back to the org default


def test_edit_updates_tool_scopes(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(client, name="E", mandate="m", plane="solo", schedule="manual")
    client.post(
        f"/agents/{aid}/edit",
        data={
            "mandate": "m",
            "schedule": "manual",
            "governance": "standard",
            "connectors": ["wiki", "files"],
        },
        follow_redirects=False,
    )
    assert _agent_of(aid).connectors == "wiki,files"


def test_run_uses_configured_max_steps(tmp_path, monkeypatch):
    import types

    from anthill.web import agents_run
    from anthill.web.db import Agent, OrgSettings

    db = _session(tmp_path, monkeypatch)
    db.add(OrgSettings(org_id=1, agent_max_steps=7))
    agent = Agent(org_id=1, created_by=1, name="Cap", mandate="m", plane="solo")
    db.add(agent)
    db.commit()
    captured = {}

    class _Spy:
        def __init__(self, backend, tools, **kw):
            captured.update(kw)

        def run(self, goal, *, context=""):
            return types.SimpleNamespace(answer="ok")

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _Spy)
    agents_run.run_agent(agent, db)
    assert captured["max_steps"] == 7  # the org run cap reaches the executor


# ── P1: live run state, chat-spawned creation, onboarding ───────────────────────


def test_sweep_closes_stale_running_runs(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from anthill.web import scheduler
    from anthill.web.db import Agent, AgentRun

    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    from fk_seed import seed_org_and_users

    S = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    _s0 = S()
    seed_org_and_users(_s0)
    _s0.commit()
    _s0.close()
    s = S()
    a = Agent(org_id=1, created_by=1, name="R", mandate="m", plane="solo")
    s.add(a)
    s.commit()
    s.add(
        AgentRun(
            org_id=1,
            agent_id=a.id,
            status="running",
            started_at=datetime.now(timezone.utc),
            finished_at=None,
        )
    )
    s.commit()
    scheduler._sweep_stale_running_runs(eng)  # simulates a restart while a run was in flight
    r = S().query(AgentRun).filter(AgentRun.agent_id == a.id).one()
    assert r.status == "error" and "interrupted" in r.error and r.finished_at is not None


def test_detail_shows_live_state_for_a_running_run(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    import anthill.web.app as app_mod
    from anthill.web.db import AgentRun

    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(client, name="Live", mandate="m", plane="solo", schedule="manual")
    s = app_mod._SessionFactory()
    s.add(
        AgentRun(
            org_id=ids["org"],
            agent_id=aid,
            status="running",
            started_at=datetime.now(timezone.utc),
            finished_at=None,
        )
    )
    s.commit()
    page = client.get(f"/agents/{aid}").text
    assert "running now" in page  # the live banner
    assert "window.location.reload" in page  # auto-refresh until it settles


def test_looks_like_agent_and_parse_agent():
    from anthill.agent import intent

    assert intent.looks_like_agent("create an agent that tracks competitor news")
    assert intent.looks_like_agent("Set up an agent to summarise my inbox each morning")
    assert not intent.looks_like_agent("create a pdf about competitors")  # not an agent
    assert not intent.looks_like_agent("what is an agent?")  # a question, not a request
    name, mandate = intent.parse_agent("create an agent that tracks competitor news")
    assert "tracks competitor news" in mandate.lower()
    assert name and len(name) <= 60


def test_chat_agent_route_creates_an_agent(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    r = client.post(
        "/chat/agent",
        data={"name": "Scout", "mandate": "track competitor news"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    a = _agent_of(r.json()["id"])
    assert a.name == "Scout" and a.mandate == "track competitor news"
    assert a.schedule == "manual" and a.plane == "solo"
    # an empty mandate is rejected
    r2 = client.post("/chat/agent", data={"name": "X", "mandate": ""}, follow_redirects=False)
    assert r2.status_code == 400


def test_agents_list_shows_running_badge_for_a_live_agent(tmp_path, monkeypatch):
    # Founder feedback 2026-09-28: live states should be visible in agent workflows, not just on the
    # one detail page you happen to have open. The list previously only ever showed active/paused.
    from datetime import datetime, timezone

    import anthill.web.app as app_mod
    from anthill.web.db import AgentRun

    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(client, name="Live One", mandate="m", plane="solo", schedule="manual")
    s = app_mod._SessionFactory()
    s.add(
        AgentRun(
            org_id=ids["org"],
            agent_id=aid,
            status="running",
            started_at=datetime.now(timezone.utc),
            finished_at=None,
        )
    )
    s.commit()
    page = client.get("/agents").text
    assert "badge-running" in page and "Live One" in page


def test_agents_list_shows_normal_status_when_nothing_is_running(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    _make(client, name="Idle One", mandate="m", plane="solo", schedule="manual")
    page = client.get("/agents").text
    assert "badge-running" not in page
    assert "badge-active" in page  # the ordinary active/paused status still shows


def test_agents_page_shows_onboarding_examples(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    page = client.get("/agents").text
    assert "Start from an example" in page and "Inbox triage" in page


# ── Run now: the Agents pages must render once an agent has a due time ─────────


def test_agents_pages_render_after_run_now(tmp_path, monkeypatch):
    # v0.12.7 QA: after "Run now", both GET /agents and GET /agents/<id> answered 500 ("can't compare
    # offset-naive and offset-aware datetimes"). SQLite hands next_run_at back naive while the pages compared
    # it with an aware "now". Run now writes a due time, and the detail page reads it on its redirect.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(client, name="Runner", mandate="m", plane="solo", schedule="manual")
    resp = client.post(f"/agents/{aid}/run-now", follow_redirects=False)
    assert resp.status_code == 302
    listing = client.get("/agents")
    detail = client.get(f"/agents/{aid}")
    assert listing.status_code == 200 and detail.status_code == 200
    # A just-triggered agent is due now, so both pages show it as live (the tick picks it up within seconds).
    assert "badge-running" in listing.text


def test_agents_pages_treat_a_future_due_time_as_not_live(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import anthill.web.app as app_mod
    from anthill.web.db import Agent

    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["a"], ids["org"], "admin")
    aid = _make(client, name="Later", mandate="m", plane="solo", schedule="manual")
    s = app_mod._SessionFactory()
    agent = s.query(Agent).filter(Agent.id == aid).first()
    agent.status = "active"
    agent.next_run_at = datetime.now(timezone.utc) + timedelta(hours=2)
    s.commit()
    listing = client.get("/agents")
    detail = client.get(f"/agents/{aid}")
    assert listing.status_code == 200 and detail.status_code == 200
    assert "badge-running" not in listing.text
