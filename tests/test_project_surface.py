"""First-class Projects P1 (#419): a chat started inside a project inherits the project's plane +
team_id (you pick the home, not a per-chat plane), and the project home lists its chats alongside its
wiki + members. Option A - Project IS the existing Team, surfaced. Model-free (no model calls)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, Team, TeamMembership, User

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
    owner = User(org_id=org.id, email="o@acme.com", role="admin", active=True)
    outsider = User(org_id=org.id, email="x@acme.com", role="member", active=True)
    s.add_all([owner, outsider])
    s.flush()
    team = Team(org_id=org.id, name="Platform", slug="platform", owner_id=owner.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=owner.id, role="owner", status="active"))
    s.commit()
    ids = {"org": org.id, "owner": owner.id, "outsider": outsider.id, "team": team.id}
    owner_c = TestClient(app_mod.app)
    owner_c.cookies.set("session_token", make_token(owner.id, org.id, "admin"))
    outsider_c = TestClient(app_mod.app)
    outsider_c.cookies.set("session_token", make_token(outsider.id, org.id, "member"))
    return app_mod, owner_c, outsider_c, ids


def test_new_chat_in_project_scopes_to_team(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    r = owner_c.post(
        "/chat/new", data={"plane": "solo", "team_id": ids["team"]}, follow_redirects=False
    )
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    conv = s.query(Conversation).filter(Conversation.user_id == ids["owner"]).first()
    # a chat opened inside a project inherits the project's plane + team_id, overriding the posted plane
    assert conv.plane == "team" and conv.team_id == ids["team"]


def test_new_chat_ignores_a_foreign_project(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    app_mod, _owner_c, outsider_c, ids = _app(tmp_path, monkeypatch)
    # the outsider is NOT a member of the team -> a stray team_id from the client is ignored, never
    # trusted to scope the chat; it falls back to the normal solo/org logic.
    r = outsider_c.post("/chat/new", data={"team_id": ids["team"]}, follow_redirects=False)
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    conv = s.query(Conversation).filter(Conversation.user_id == ids["outsider"]).first()
    assert conv.team_id is None and conv.plane == "solo"


def test_project_home_lists_its_chats(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(
        Conversation(
            org_id=ids["org"],
            user_id=ids["owner"],
            plane="team",
            team_id=ids["team"],
            title="Sprint planning",
        )
    )
    s.commit()
    r = owner_c.get(f"/teams/{ids['team']}")
    assert r.status_code == 200
    body = r.text
    assert "Chats in this project" in body  # the project home has a chats section...
    assert "Sprint planning" in body  # ...listing this project's chat...
    assert "New chat in this project" in body  # ...and a way to start one
    assert "Project wiki" in body  # bidirectional: wiki reachable from the project home


def test_project_home_hides_other_users_chats(tmp_path, monkeypatch):
    from anthill.web.db import Conversation, TeamMembership

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    # the outsider joins the team and has a chat in it; the owner's project home shows only their own
    s.add(
        TeamMembership(team_id=ids["team"], user_id=ids["outsider"], role="member", status="active")
    )
    s.add(
        Conversation(
            org_id=ids["org"],
            user_id=ids["outsider"],
            plane="team",
            team_id=ids["team"],
            title="Outsider secret chat",
        )
    )
    s.commit()
    body = owner_c.get(f"/teams/{ids['team']}").text
    assert "Outsider secret chat" not in body  # P1 scopes the list to the current user's own chats


# ── Project settings page (P1): rename / delete / members-only-if-org ────────────


def test_project_settings_owner_can_rename(tmp_path, monkeypatch):
    from anthill.web.db import Team

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    r = owner_c.post(
        f"/teams/{ids['team']}/rename", data={"name": "Growth Squad"}, follow_redirects=False
    )
    assert r.status_code == 302
    t = app_mod._SessionFactory().query(Team).filter(Team.id == ids["team"]).first()
    assert t.name == "Growth Squad" and t.slug == "growth-squad"  # slug follows the name


def test_project_settings_non_owner_is_forbidden(tmp_path, monkeypatch):
    _app_mod, _owner_c, outsider_c, ids = _app(tmp_path, monkeypatch)
    tid = ids["team"]
    assert outsider_c.get(f"/teams/{tid}/settings").status_code == 403
    assert (
        outsider_c.post(
            f"/teams/{tid}/rename", data={"name": "x"}, follow_redirects=False
        ).status_code
        == 403
    )
    assert outsider_c.post(f"/teams/{tid}/delete", follow_redirects=False).status_code == 403


def test_project_delete_keeps_work_and_removes_only_pending_reviews(tmp_path, monkeypatch):
    from anthill.web.db import Agent, Conversation, ScheduledTask, Team, TeamMembership, WikiReview

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    tid, org, uid = ids["team"], ids["org"], ids["owner"]
    s = app_mod._SessionFactory()
    s.add(Conversation(org_id=org, user_id=uid, plane="team", team_id=tid, title="keep chat"))
    s.add(
        ScheduledTask(
            org_id=org, created_by=uid, title="keep task", goal="g", plane="team", team_id=tid
        )
    )
    s.add(Agent(org_id=org, created_by=uid, name="keep agent", plane="team", team_id=tid))
    s.add(
        WikiReview(
            org_id=org, slug="pend", content="c", target_scope="team", team_id=tid, status="pending"
        )
    )
    s.add(
        WikiReview(
            org_id=org,
            slug="done",
            content="c",
            target_scope="team",
            team_id=tid,
            status="approved",
        )
    )
    s.commit()

    assert owner_c.post(f"/teams/{tid}/delete", follow_redirects=False).status_code == 302
    s = app_mod._SessionFactory()
    assert s.query(Team).filter(Team.id == tid).first() is None  # the project is gone
    assert s.query(TeamMembership).filter(TeamMembership.team_id == tid).count() == 0
    # chats / tasks / agents are kept, reset to Solo - nothing a person made is destroyed
    conv = s.query(Conversation).filter(Conversation.title == "keep chat").one()
    task = s.query(ScheduledTask).filter(ScheduledTask.title == "keep task").one()
    agent = s.query(Agent).filter(Agent.name == "keep agent").one()
    for row in (conv, task, agent):
        assert row.team_id is None and row.plane == "solo"
    # only the PENDING review is removed; a completed review survives as audit history, with its
    # team_id cleared so it does not dangle against the deleted team (FK-safe)
    assert s.query(WikiReview).filter(WikiReview.slug == "pend").count() == 0
    done = s.query(WikiReview).filter(WikiReview.slug == "done").one()
    assert done.team_id is None


def test_members_are_gated_on_an_org_project(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)
    # Solo project (no org backend): the invite is hidden and settings call it a Solo project.
    assert "+ Invite member" not in owner_c.get(f"/teams/{ids['team']}").text
    assert "Solo project" in owner_c.get(f"/teams/{ids['team']}/settings").text
    # once an org backend is connected it is an org project: invite appears, settings say Org project.
    s = app_mod._SessionFactory()
    s.add(
        OrgSettings(
            org_id=ids["org"], org_backend_status="validated", org_model_endpoint="https://e/v1"
        )
    )
    s.commit()
    assert "+ Invite member" in owner_c.get(f"/teams/{ids['team']}").text
    assert "Org project" in owner_c.get(f"/teams/{ids['team']}/settings").text


def test_invite_endpoint_enforces_org_mode_not_just_the_ui(tmp_path, monkeypatch):
    # The members-only-if-org rule is enforced at the ROUTE, not just by hiding the button: a direct POST
    # to a Solo project's invite endpoint is rejected and creates no membership (ASDD review of #459).
    from anthill.web.db import OrgSettings, TeamMembership

    app_mod, owner_c, _outsider_c, ids = _app(tmp_path, monkeypatch)

    def _member_count():
        return (
            app_mod._SessionFactory()
            .query(TeamMembership)
            .filter(
                TeamMembership.team_id == ids["team"],
                TeamMembership.user_id == ids["outsider"],
            )
            .count()
        )

    r = owner_c.post(
        f"/teams/{ids['team']}/invite", data={"email": "x@acme.com"}, follow_redirects=False
    )
    assert r.status_code == 302 and "solo_no_members" in r.headers["location"]
    assert _member_count() == 0  # no membership created despite a direct POST
    # the redirect target actually renders the explanation (team_detail reads ?error= via `err`)
    assert (
        "Set up an organization to invite members"
        in owner_c.get(f"/teams/{ids['team']}?error=solo_no_members").text
    )

    # once an org backend is connected the same invite is accepted
    s = app_mod._SessionFactory()
    s.add(
        OrgSettings(
            org_id=ids["org"], org_backend_status="validated", org_model_endpoint="https://e/v1"
        )
    )
    s.commit()
    owner_c.post(
        f"/teams/{ids['team']}/invite", data={"email": "x@acme.com"}, follow_redirects=False
    )
    assert _member_count() == 1
