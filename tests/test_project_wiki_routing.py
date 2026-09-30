"""Per-project wiki routing (#419 P2 / Gap A): a run inside a project (plane=team + team_id) grounds in
and writes to THAT project's own wiki (team-<id>) only - not the personal/org wiki, and not every team
the user belongs to. Non-project runs keep their broad grounding. Model-free."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    import anthill.web.app as app_mod

    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return app_mod._SessionFactory()


# ── read / grounding: context_workspaces scopes a project run to its own wiki ────


def test_context_workspaces_scopes_a_project_run_to_its_own_wiki(tmp_path, monkeypatch):
    from anthill.web.agent_context import context_workspaces
    from anthill.web.db import Organization, Team, TeamMembership, User

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    t1 = Team(org_id=org.id, name="One", slug="one", owner_id=u.id)
    t2 = Team(org_id=org.id, name="Two", slug="two", owner_id=u.id)
    s.add_all([t1, t2])
    s.flush()
    for t in (t1, t2):
        s.add(TeamMembership(team_id=t.id, user_id=u.id, role="owner", status="active"))
    s.commit()

    # In project t1 -> only t1's wiki grounds the run, not t2's.
    scoped = context_workspaces(s, user_id=u.id, plane="team", team_id=t1.id, is_org=False)
    teams = [label for tier, label, _ws in scoped if tier == "team"]
    assert teams == [f"Team {t1.id}"]

    # A Solo run keeps the broad "all your teams" grounding (not project-scoped).
    scoped_solo = context_workspaces(s, user_id=u.id, plane="solo", team_id=None, is_org=False)
    teams_solo = sorted(label for tier, label, _ws in scoped_solo if tier == "team")
    assert teams_solo == [f"Team {t1.id}", f"Team {t2.id}"]


# ── write: a project agent's tools write to team-<id>, not personal/org ──────────


def _plane_inf(wiki_scope, use_personal_context):
    from anthill.web.plane_routing import PlaneInference

    return PlaneInference(
        plane="team" if wiki_scope == "team" else "solo",
        backend="ollama",
        base_url="http://x:11434",
        model="m",
        api_key=None,
        wiki_scope=wiki_scope,
        use_personal_context=use_personal_context,
    )


def test_project_agent_writes_to_its_own_project_wiki(tmp_path, monkeypatch):
    from anthill.web.agents_run import _plane_tools
    from anthill.web.db import Agent, Organization, Team, TeamMembership, User

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    team = Team(org_id=org.id, name="P", slug="p", owner_id=u.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=u.id, role="owner", status="active"))
    agent = Agent(org_id=org.id, created_by=u.id, name="proj", plane="team", team_id=team.id)
    solo = Agent(org_id=org.id, created_by=u.id, name="solo", plane="solo", team_id=None)
    s.add_all([agent, solo])
    s.commit()

    _tools, ws_path = _plane_tools(s, agent, _plane_inf("team", True))
    assert ws_path.replace("\\", "/").endswith(f"team-{team.id}")  # its own project wiki (a member)

    # a Solo agent writes to the default/personal workspace, never a team dir
    _t2, ws_solo = _plane_tools(s, solo, _plane_inf("personal", True))
    assert "team-" not in ws_solo


def test_solo_agent_reads_the_creators_own_personal_wiki_not_the_legacy_workspace(
    tmp_path, monkeypatch
):
    """#838: _plane_tools built ws_path via run_wiki_workspace WITHOUT personal_user_id, so a Solo
    agent fell all the way through to the legacy single-node ANTHILL_WORKSPACE instead of the
    per-user wiki (wikis/user-<id>) that /wiki/upload, snippet-to-wiki, and memory-to-wiki all write
    to - only Chat (which does pass personal_user_id) could see that knowledge. The test above only
    asserted 'not a team dir', which the legacy workspace also satisfies - it passed whichever
    fallback the code took, and so never caught this."""
    from anthill.web.agents_run import _plane_tools
    from anthill.web.db import Agent, Organization, User
    from anthill.wiki.workspace import workspace_for

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    agent = Agent(org_id=org.id, created_by=u.id, name="solo", plane="solo", team_id=None)
    s.add(agent)
    s.commit()

    _tools, ws_path = _plane_tools(s, agent, _plane_inf("personal", True))
    assert ws_path == str(workspace_for("personal", user_id=u.id).root)


def test_solo_task_reads_the_creators_own_personal_wiki_not_the_legacy_workspace(
    tmp_path, monkeypatch
):
    """#838: same bug, the scheduler's call site - _run_task built ws_path without personal_user_id
    either, so a Solo task's tools were scoped to the legacy workspace too."""
    from unittest.mock import patch

    from anthill.web import scheduler
    from anthill.web.db import Organization, ScheduledTask, User
    from anthill.wiki.workspace import workspace_for

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    task = ScheduledTask(
        org_id=org.id,
        created_by=u.id,
        title="T",
        goal="g",
        schedule="once",
        status="pending",
        plane="solo",
    )
    s.add(task)
    s.commit()

    captured = {}

    def _fake_make_tools(*, workspace, owner):
        captured["workspace"] = workspace
        return []

    class _StubExecutor:
        def __init__(self, backend, tools, **kw):
            pass

        def run(self, goal, *, context=""):
            import types

            return types.SimpleNamespace(answer="ok")

    with (
        patch("anthill.agent.tools.make_tools", _fake_make_tools),
        patch("anthill.agent.executor.AgentExecutor", _StubExecutor),
    ):
        scheduler._run_task(task, s)

    assert captured["workspace"] == str(workspace_for("personal", user_id=u.id).root)


def test_project_routing_denies_a_project_the_user_is_not_in(tmp_path, monkeypatch):
    # The trust boundary: a team-plane run pointed at a project the user is NOT an active member of
    # never grounds in or writes to that project's wiki (a manipulated team_id can't reach it).
    from anthill.web.agent_context import context_workspaces
    from anthill.web.agents_run import _plane_tools
    from anthill.web.db import Agent, Organization, Team, TeamMembership, User

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    mine = Team(org_id=org.id, name="Mine", slug="mine", owner_id=u.id)
    other = Team(org_id=org.id, name="Other", slug="other", owner_id=u.id)  # user is NOT a member
    s.add_all([mine, other])
    s.flush()
    s.add(TeamMembership(team_id=mine.id, user_id=u.id, role="owner", status="active"))
    s.commit()

    # grounding: a run pointed at the foreign project never grounds in it; it falls back to the broad
    # non-project path (the user's OWN active teams), so a stale/manipulated team_id sees only their own.
    scoped = context_workspaces(s, user_id=u.id, plane="team", team_id=other.id, is_org=False)
    team_labels = [label for tier, label, _w in scoped if tier == "team"]
    assert team_labels == [f"Team {mine.id}"]
    assert f"Team {other.id}" not in team_labels

    # write: an agent whose creator is not a member of team `other` never writes to team-<other>
    # (a single write target, so it falls back to the personal/default workspace, not a foreign wiki)
    agent = Agent(org_id=org.id, created_by=u.id, name="x", plane="team", team_id=other.id)
    s.add(agent)
    s.commit()
    _tools, ws_path = _plane_tools(s, agent, _plane_inf("team", True))
    assert f"team-{other.id}" not in ws_path


def test_run_wiki_workspace_resolves_every_surface(tmp_path, monkeypatch):
    # The one shared resolver used by chat, tasks, and agents - all write/read-base routing goes through
    # it, so one test covers every surface's routing + the trust boundary.
    from anthill.web.agent_context import run_wiki_workspace
    from anthill.web.db import Organization, Team, TeamMembership, User

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()
    t = Team(org_id=org.id, name="P", slug="p", owner_id=u.id)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=u.id, role="owner", status="active"))
    s.commit()

    def norm(p):
        return p.replace("\\", "/")

    # project run by a member -> the project's own wiki
    assert norm(
        run_wiki_workspace(s, plane_inf=_plane_inf("team", True), team_id=t.id, member_user_id=u.id)
    ).endswith(f"team-{t.id}")
    # project run by a NON-member -> never the project wiki (trust boundary)
    assert f"team-{t.id}" not in run_wiki_workspace(
        s, plane_inf=_plane_inf("team", True), team_id=t.id, member_user_id=424242
    )
    # org run -> the shared org wiki
    assert norm(
        run_wiki_workspace(s, plane_inf=_plane_inf("org", False), team_id=None, member_user_id=u.id)
    ).endswith("org")
    # Solo task/agent (no personal_user_id) -> the single-node ANTHILL_WORKSPACE
    assert norm(
        run_wiki_workspace(
            s, plane_inf=_plane_inf("personal", True), team_id=None, member_user_id=u.id
        )
    ).endswith("/ws")
    # a MISSING/unavailable plane (PlaneUnavailable) falls back to personal/default (user-<id>), never
    # the org wiki - a degraded run must not read/write the shared org wiki
    assert norm(
        run_wiki_workspace(
            s, plane_inf=None, team_id=None, member_user_id=u.id, personal_user_id=u.id
        )
    ).endswith(f"user-{u.id}")
    # Solo chat (personal_user_id given) -> the per-user personal wiki
    assert norm(
        run_wiki_workspace(
            s,
            plane_inf=_plane_inf("personal", True),
            team_id=None,
            member_user_id=u.id,
            personal_user_id=u.id,
        )
    ).endswith(f"user-{u.id}")


# ── chat_stream's ask() base wiki: must match ws_path, not re-derive it ──────────


def test_chat_stream_grounds_a_project_chat_in_its_own_team_wiki(tmp_path, monkeypatch):
    """Found live by a sibling agent session: a chat INSIDE a project answered "I couldn't find
    specific information..." even though the fact was correctly written to the team's own wiki.
    Root cause - chat_stream's ask() base wiki was re-resolved independently of ws_path (used for
    writes/tools) and only ever fell back to personal/org, never team - so team-scoped chat never
    grounds in its own project's wiki. This exercises the real /chat/{id}/stream route end to end."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    import anthill.wiki.ask as wiki_ask
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, Team, TeamMembership, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo")])
    s.flush()
    team = Team(org_id=o.id, name="P", slug="p", owner_id=u.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=u.id, role="owner", status="active"))
    conv = Conversation(user_id=u.id, org_id=o.id, plane="team", team_id=team.id, title="t")
    s.add(conv)
    s.commit()
    conv_id, team_id = conv.id, team.id

    captured = {}

    def _fake_ask_stream(ws, message, backend, **kw):
        captured["ws_root"] = str(ws.root).replace("\\", "/")
        yield "ok"

    monkeypatch.setattr(wiki_ask, "ask_stream", _fake_ask_stream)

    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    r = c.get(f"/chat/{conv_id}/stream", params={"message": "what's the reactor temperature?"})
    assert r.status_code == 200
    assert captured["ws_root"].endswith(f"team-{team_id}")  # its own project wiki, not personal/org
