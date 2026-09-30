"""Governed skill auto-distillation (#375): after a successful agent run, a reusable skill is
distilled and queued as a pending ProposedSkill (never written live) for human accept/reject.
Model-free (the distillation model call is stubbed)."""

import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _engine(tmp_path, name="s.db"):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    _s = sessionmaker(bind=eng)()
    seed_org_and_users(_s)  # org 1 + users so any session on this engine has valid parents
    _s.commit()
    _s.close()
    return eng


def _session(tmp_path):
    return sessionmaker(bind=_engine(tmp_path), autoflush=False, autocommit=False)()


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "skills"))
    eng = _engine(tmp_path, "app.db")
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(u)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return client, {"org": org.id, "u": u.id}


# ── the distiller (unit) ────────────────────────────────────────────────────────


def test_distil_skill_parses_candidate_and_skip(monkeypatch):
    from anthill.agent import skills

    monkeypatch.setattr(
        skills,
        "json_chat",
        lambda backend, msgs: (
            '{"name":"Track","description":"d","when_to_use":"w","instructions":"steps"}'
        ),
    )
    got = skills.distil_skill("goal", "result", None)
    assert got and got["name"] == "Track" and got["instructions"] == "steps"
    # a one-off run that can't generalise -> None (nothing queued)
    monkeypatch.setattr(skills, "json_chat", lambda backend, msgs: '{"skip": true}')
    assert skills.distil_skill("goal", "result", None) is None


# ── the trigger: propose-only, gated, deduped ───────────────────────────────────


def _stub_distil(monkeypatch, name="Track Competitors"):
    from anthill.web import scheduler  # noqa: F401 (ensures module import)

    monkeypatch.setattr(
        "anthill.agent.skills.distil_skill",
        lambda goal, result, backend: {
            "name": name,
            "description": "d",
            "when_to_use": "w",
            "instructions": "the general steps",
        },
    )
    monkeypatch.setattr("anthill.inference.base.build_backend", lambda config: object())
    monkeypatch.setattr(
        "anthill.web.plane_routing.plane_inference",
        lambda plane, cfg, decrypt=None: types.SimpleNamespace(
            backend="ollama", base_url="", model="qwen", api_key="", use_personal_context=True
        ),
    )


def test_distil_trigger_queues_a_pending_proposal_and_dedups(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import Agent, OrgSettings, ProposedSkill

    db = _session(tmp_path)
    db.add(OrgSettings(org_id=1, skill_autolearn=True))
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="track competitors", plane="solo")
    db.add(agent)
    db.commit()
    _stub_distil(monkeypatch)

    scheduler._distil_skill_from_agent(agent, "ran ok", db)
    props = db.query(ProposedSkill).filter(ProposedSkill.agent_id == agent.id).all()
    assert len(props) == 1
    assert props[0].status == "pending" and props[0].scope == "personal"
    assert props[0].instructions == "the general steps"
    # a second, similar distillation must NOT queue a duplicate (same conformed name)
    scheduler._distil_skill_from_agent(agent, "ran ok again", db)
    assert db.query(ProposedSkill).filter(ProposedSkill.agent_id == agent.id).count() == 1


def test_distil_trigger_respects_autolearn_off(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import Agent, OrgSettings, ProposedSkill

    db = _session(tmp_path)
    db.add(OrgSettings(org_id=1, skill_autolearn=False))  # paused
    agent = Agent(org_id=1, created_by=1, name="Scout", mandate="m", plane="solo")
    db.add(agent)
    db.commit()
    _stub_distil(monkeypatch)
    scheduler._distil_skill_from_agent(agent, "ran ok", db)
    assert db.query(ProposedSkill).count() == 0  # nothing proposed while paused


# ── accept / reject / toggle (routes) ───────────────────────────────────────────


def test_accept_and_reject_proposal(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import ProposedSkill

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    p1 = ProposedSkill(
        org_id=ids["org"],
        created_by=ids["u"],
        scope="personal",
        name="MySkill",
        description="d",
        when_to_use="w",
        instructions="do the thing",
        status="pending",
    )
    p2 = ProposedSkill(
        org_id=ids["org"],
        created_by=ids["u"],
        scope="personal",
        name="Other",
        instructions="x",
        status="pending",
    )
    s.add_all([p1, p2])
    s.commit()
    p1id, p2id = p1.id, p2.id

    assert client.post(f"/skills/proposed/{p1id}/accept", follow_redirects=False).status_code == 302
    assert client.post(f"/skills/proposed/{p2id}/reject", follow_redirects=False).status_code == 302
    rows = {r.id: r.status for r in app_mod._SessionFactory().query(ProposedSkill).all()}
    assert rows[p1id] == "accepted" and rows[p2id] == "rejected"
    # the accepted skill is a real skill now + the queue no longer shows either
    page = client.get("/skills").text
    assert "MySkill" in page and "Proposed skills" not in page


def test_autolearn_toggle(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(OrgSettings(org_id=ids["org"]))  # every real org has one (default skill_autolearn=True)
    s.commit()
    client.post("/skills/autolearn-toggle", follow_redirects=False)
    cfg = (
        app_mod._SessionFactory()
        .query(OrgSettings)
        .filter(OrgSettings.org_id == ids["org"])
        .first()
    )
    assert cfg.skill_autolearn is False  # default True -> paused
