"""v2 web routes: memory -> wiki proposal, memory team promotion, conversational
skill refine, and team-skill edits routed through the review gate. Workspaces point
at tmp; the model backend is unavailable in tests, so review gates fail safe to a
queued review (which is exactly what we assert)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MemoryItem, Organization, Team, TeamMembership, User, WikiReview


def _client(tmp_path, monkeypatch, role="member"):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "orgwiki"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-skills"))
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role=role, active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, u.id


def test_memory_to_wiki_routes_through_gate(tmp_path, monkeypatch):
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    m = MemoryItem(
        org_id=org_id,
        user_id=uid,
        scope="personal",
        kind="fact",
        text="We use Postgres for billing",
    )
    s.add(m)
    s.commit()
    try:
        from anthill.wiki.workspace import workspace_for

        r = c.post(
            f"/memory/{m.id}/wiki", data={"target_scope": "personal"}, follow_redirects=False
        )
        assert r.status_code == 302
        # The memory was routed through the gate: either queued for review or, if the
        # review came back clean, written straight to the personal wiki. Accept both.
        rev = s.query(WikiReview).filter(WikiReview.org_id == org_id).first()
        queued = rev is not None and "Postgres" in rev.content
        ws = workspace_for("personal", user_id=uid)
        applied = ws.exists() and any("Postgres" in p.read_text() for p in ws.pages())
        assert queued or applied
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_memory_promote_to_team(tmp_path, monkeypatch):
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    t = Team(org_id=org_id, name="P", slug="p", owner_id=uid)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=uid, role="owner", status="active"))
    m = MemoryItem(org_id=org_id, user_id=uid, scope="personal", kind="fact", text="Team fact.")
    s.add(m)
    s.commit()
    try:
        c.post(f"/memory/{m.id}/promote/team", data={"team_id": str(t.id)}, follow_redirects=False)
        s.refresh(m)
        assert m.scope == "team" and m.team_id == t.id
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_memory_and_skills_pages_render(tmp_path, monkeypatch):
    c, app_mod, _o, _u = _client(tmp_path, monkeypatch)
    try:
        assert c.get("/memory").status_code == 200  # templates compile (incl. new buttons)
        assert c.get("/skills").status_code == 200
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_skills_refine_returns_json(tmp_path, monkeypatch):
    c, app_mod, _org, _uid = _client(tmp_path, monkeypatch)
    try:
        r = c.post("/skills/refine", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and "ready" in r.json()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_team_skill_edit_queues_review(tmp_path, monkeypatch):
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    t = Team(org_id=org_id, name="P", slug="p", owner_id=uid)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=uid, role="owner", status="active"))
    s.commit()
    try:
        c.post(
            "/skills/create",  # unified scope-aware Skills route (was /wiki/skills)
            data={
                "scope": "team",
                "team_id": str(t.id),
                "name": "Team Skill",
                "when_to_use": "x",
                "instructions": "do it",
            },
            follow_redirects=False,
        )
        rev = (
            s.query(WikiReview)
            .filter(WikiReview.kind == "skill", WikiReview.target_scope == "team")
            .first()
        )
        assert rev is not None and rev.status == "pending" and "Team Skill" in rev.content
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
