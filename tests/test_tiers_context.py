"""Solo/Team/Org tiers - plane-aware context (PR-B). The principles/skills scope MIRRORS the chat
read-side wiki grounding: org (when an org) + the user's teams (always) + personal only on LOCAL
compute - so personal context never reaches the org cloud (the privacy leak fix)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as dbmod
from anthill.web.agent_context import context_workspaces
from anthill.web.db import Organization, Team, TeamMembership, User


def _db_with_team(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    dbmod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    t = Team(org_id=o.id, name="Proj", slug="proj", owner_id=u.id)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=u.id, role="owner", status="active"))
    s.commit()
    return s, u.id


def _tiers(ws):
    return [t for t, _label, _w in ws]


def test_solo_in_org_layers_org_team_personal(tmp_path):
    s, uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=uid, plane="solo", is_org=True)) == [
        "org",
        "team",
        "personal",
    ]


def test_org_plane_has_org_and_team_but_no_personal(tmp_path):
    # THE LEAK FIX: an org-plane (cloud) run never includes the personal tier
    s, uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=uid, plane="org", is_org=True)) == ["org", "team"]


def test_team_in_org_excludes_personal(tmp_path):
    s, uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=uid, plane="team", is_org=True)) == ["org", "team"]


def test_team_in_solo_includes_personal_no_org(tmp_path):
    s, uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=uid, plane="team", is_org=False)) == [
        "team",
        "personal",
    ]


def test_solo_deployment_has_no_org_wiki(tmp_path):
    s, uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=uid, plane="solo", is_org=False)) == [
        "team",
        "personal",
    ]


def test_a2a_org_no_user_is_org_only(tmp_path):
    s, _uid = _db_with_team(tmp_path)
    assert _tiers(context_workspaces(s, user_id=None, plane="org", is_org=True)) == ["org"]


def test_agent_context_for_org_plane_excludes_personal(tmp_path, monkeypatch):
    s, uid = _db_with_team(tmp_path)
    from anthill.agent import skills as skills_mod
    from anthill.web import agent_context
    from anthill.wiki import principles as principles_mod

    seen = {}
    monkeypatch.setattr(
        principles_mod,
        "assemble_principles",
        lambda items: seen.setdefault("p", [lbl for lbl, _ in items]),
    )
    monkeypatch.setattr(
        skills_mod, "load_skills", lambda scoped: seen.setdefault("s", [t for t, _ in scoped])
    )
    agent_context.agent_context_for(s, user_id=uid, org_id=1, plane="org", is_org=True)
    assert "Personal" not in seen["p"] and "personal" not in seen["s"]  # leak fixed
    assert "Organization" in seen["p"] and "team" in seen["s"]  # org + team still present
