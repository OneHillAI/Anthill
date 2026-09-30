"""The unified, scope-aware Skills page (/skills): it loads the skills of every scope the user is in
(the same SKILL.md skills the agent + chat load), and lets them add to a scope they can edit. Personal
applies directly; org/team route through the review gate. (Replaces the old wiki Skills section.)"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User, WikiReview


def _admin(tmp_path, monkeypatch, role="admin"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-skills"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role=role, active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, u.id


def test_skills_page_offers_scope_selector(tmp_path, monkeypatch):
    c, _, _, _ = _admin(tmp_path, monkeypatch)
    page = c.get("/skills").text
    assert page.count('action="/skills/create"')  # the scope-aware create form
    assert 'name="scope"' in page and "Personal" in page
    assert "Organization (everyone)" in page  # admin can target the org scope


def test_personal_skill_applies_directly_and_lists(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, _, _, uid = _admin(tmp_path, monkeypatch, role="member")
    r = c.post(
        "/skills/create",
        data={"scope": "personal", "name": "Daily Standup", "instructions": "summarize blockers"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert (workspace_for("personal", user_id=uid).skills / "daily-standup" / "SKILL.md").exists()
    assert "Daily Standup" in c.get("/skills").text  # shows on the page


def test_org_skill_queues_review(tmp_path, monkeypatch):
    c, app_mod, _org_id, _uid = _admin(tmp_path, monkeypatch)  # admin
    c.post(
        "/skills/create",
        data={"scope": "org", "name": "Org Voice", "instructions": "use our house style"},
        follow_redirects=False,
    )
    # the model backend is down in tests, so the review gate fails safe to a queued review
    rev = (
        app_mod._SessionFactory()
        .query(WikiReview)
        .filter(WikiReview.kind == "skill", WikiReview.target_scope == "org")
        .first()
    )
    assert rev is not None and rev.status == "pending" and "Org Voice" in rev.content


def test_member_cannot_create_org_skill(tmp_path, monkeypatch):
    c, _, _, _ = _admin(tmp_path, monkeypatch, role="member")
    r = c.post(
        "/skills/create",
        data={"scope": "org", "name": "X", "instructions": "y"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 403)  # members can't write the org scope
    # nothing rendered for org targeting either
    assert "Organization (everyone)" not in c.get("/skills").text
