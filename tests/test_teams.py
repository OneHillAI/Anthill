"""Team / project tier + agent-assisted wiki review.

Covers the schema migration, the workspace path resolver, team CRUD + invites,
the generalized promotion ladder, the agent review/auto-apply gate, and read-side
scoping. Model-free: any backend is stubbed.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from anthill.web import db, migrate
from anthill.web.db import MemoryItem, Snippet, Team, TeamMembership, WikiReview


def _session(tmp_path):
    from fk_seed import seed_org_and_users

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.create_tables(engine)
    s = sessionmaker(bind=engine)()
    seed_org_and_users(s)  # org 1 + users 1,2 (team owner/members)
    s.commit()
    return s


# ── Step 1: schema ────────────────────────────────────────────────────────────


def test_ensure_columns_adds_team_fields_to_old_db(tmp_path):
    """An old DB (pre-teams) gains the new tables + columns on startup."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:  # minimal old-schema tables, missing the new columns
        c.execute(
            text(
                "CREATE TABLE wiki_reviews (id INTEGER PRIMARY KEY, org_id INTEGER, slug VARCHAR, content TEXT)"
            )
        )
        c.execute(text("CREATE TABLE snippets (id INTEGER PRIMARY KEY, scope VARCHAR)"))
        c.execute(text("CREATE TABLE memory_items (id INTEGER PRIMARY KEY, scope VARCHAR)"))
        c.execute(text("CREATE TABLE training_examples (id INTEGER PRIMARY KEY, scope VARCHAR)"))

    added = migrate.ensure_columns(engine)
    db.create_tables(engine)  # brand-new tables (teams, team_memberships)

    ins = inspect(engine)
    tables = set(ins.get_table_names())
    assert {"teams", "team_memberships"} <= tables
    wr = {c["name"] for c in ins.get_columns("wiki_reviews")}
    assert {"target_scope", "team_id", "outline", "flags"} <= wr
    for t in ("snippets", "memory_items", "training_examples"):
        assert "team_id" in {c["name"] for c in ins.get_columns(t)}
    assert "wiki_reviews.target_scope" in added


def test_team_scoped_rows_roundtrip(tmp_path):
    db_ = _session(tmp_path)
    team = Team(org_id=1, name="Platform", slug="platform", owner_id=1)
    db_.add(team)
    db_.flush()
    db_.add(TeamMembership(team_id=team.id, user_id=1, role="owner", status="active"))
    db_.add(Snippet(org_id=1, user_id=1, content="x", scope="team", team_id=team.id))
    db_.add(MemoryItem(org_id=1, user_id=1, text="y", scope="team", team_id=team.id))
    db_.add(WikiReview(org_id=1, slug="p", content="c", target_scope="team", team_id=team.id))
    db_.commit()

    assert db_.query(Snippet).filter_by(scope="team", team_id=team.id).count() == 1
    assert db_.query(MemoryItem).filter_by(scope="team", team_id=team.id).count() == 1
    assert db_.query(WikiReview).filter_by(target_scope="team").one().team_id == team.id


# ── Step 2: path resolver ───────────────────────────────────────────────────────


def test_workspace_for_paths(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    assert workspace_for("org", org_id=1).root == tmp_path / "org"  # organisation 1 owns the folder
    assert workspace_for("team", team_id=7).root == tmp_path / "wikis" / "team-7"
    assert workspace_for("personal", user_id=3).root == tmp_path / "wikis" / "user-3"
    # personal with no user -> the single-node alpha default (legacy behavior kept)
    assert workspace_for("personal").root == tmp_path / "ws"


# ── Step 3: team CRUD + invites + auth helpers ──────────────────────────────────


def _app_client(tmp_path):
    """A TestClient bound to a fresh temp engine. Not used as a context manager,
    so the app's startup (scheduler) never fires."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings, User

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    owner = User(org_id=org.id, email="owner@acme.com", role="member", active=True)
    member = User(org_id=org.id, email="member@acme.com", role="member", active=True)
    s.add_all([owner, member])
    # A multi-member team is an ORG project: members require an org backend (is_org_mode). Configure one
    # so the invite flow is exercised (members-only-if-org is enforced at the route now, #419 review).
    s.add(
        OrgSettings(
            org_id=org.id, org_backend_status="validated", org_model_endpoint="https://e/v1"
        )
    )
    s.commit()
    ids = {"org": org.id, "owner": owner.id, "member": member.id}
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, app_mod, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_team_lifecycle_and_owner_gating(tmp_path):
    from anthill.web.db import Team, TeamMembership

    client, app_mod, ids = _app_client(tmp_path)
    try:
        # owner creates a team
        _auth(client, app_mod, ids["owner"], ids["org"])
        r = client.post("/teams", data={"name": "Platform"}, follow_redirects=False)
        assert r.status_code == 302
        team_id = int(r.headers["location"].rsplit("/", 1)[1])

        s = app_mod._SessionFactory()
        assert s.query(Team).filter_by(id=team_id).one().owner_id == ids["owner"]
        assert (
            s.query(TeamMembership)
            .filter_by(team_id=team_id, user_id=ids["owner"], role="owner", status="active")
            .count()
            == 1
        )

        # invite an existing org member -> invited row
        r = client.post(
            f"/teams/{team_id}/invite", data={"email": "member@acme.com"}, follow_redirects=False
        )
        assert r.status_code == 302 and "invited=" in r.headers["location"]
        s = app_mod._SessionFactory()
        assert (
            s.query(TeamMembership)
            .filter_by(team_id=team_id, user_id=ids["member"], status="invited")
            .count()
            == 1
        )

        # decision 3: a non-org email is rejected, no membership created
        r = client.post(
            f"/teams/{team_id}/invite", data={"email": "stranger@other.com"}, follow_redirects=False
        )
        assert r.status_code == 302 and "error=not_org_member" in r.headers["location"]

        # member accepts -> active
        _auth(client, app_mod, ids["member"], ids["org"])
        r = client.post(f"/teams/{team_id}/accept", follow_redirects=False)
        assert r.status_code == 302
        s = app_mod._SessionFactory()
        assert (
            s.query(TeamMembership)
            .filter_by(team_id=team_id, user_id=ids["member"], status="active")
            .count()
            == 1
        )

        # owner-only actions are 403 for a plain member
        assert (
            client.post(f"/teams/{team_id}/invite", data={"email": "x@acme.com"}).status_code == 403
        )
        assert client.post(f"/teams/{team_id}/members/{ids['owner']}/remove").status_code == 403

        # a member can view the team; a non-member cannot
        assert client.get(f"/teams/{team_id}").status_code == 200
        from anthill.web.db import User

        s = app_mod._SessionFactory()
        outsider = User(org_id=ids["org"], email="out@acme.com", role="member", active=True)
        s.add(outsider)
        s.commit()
        _auth(client, app_mod, outsider.id, ids["org"])
        assert client.get(f"/teams/{team_id}").status_code == 403
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


# ── Step 5: agent review + auto-apply gate ──────────────────────────────────────


class _FakeBackend:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, messages):
        return self._reply


class _DownBackend:
    def chat(self, messages):
        raise RuntimeError("backend down")


def _wiki(tmp_path):
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "wiki")
    ws.init()
    return ws


def test_outline_clean_change_auto_applies(tmp_path):
    from anthill.wiki.review import outline_change

    o = outline_change(
        _FakeBackend('{"summary":"Adds X.","flags":[]}'),
        _wiki(tmp_path),
        "new-topic",
        "# New topic\n\nA fact.\n",
        scope="org",
    )
    assert o.flags == [] and o.recommendation == "approve"


def test_outline_model_flag_queues(tmp_path):
    from anthill.wiki.review import outline_change

    o = outline_change(
        _FakeBackend('{"summary":"Conflicts.","flags":["contradiction"]}'),
        _wiki(tmp_path),
        "topic",
        "# Topic\n\nClaim.\n",
        scope="org",
    )
    assert "contradiction" in o.flags and o.recommendation == "needs_edit"


def test_outline_backend_down_fails_safe(tmp_path):
    from anthill.wiki.review import outline_change

    o = outline_change(_DownBackend(), _wiki(tmp_path), "topic", "# T\n\nClaim.\n", scope="org")
    assert "needs_edit" in o.flags  # fail safe: queued, never silently auto-applied


def test_outline_broken_links_flagged(tmp_path):
    from anthill.wiki.review import outline_change

    o = outline_change(
        _FakeBackend('{"summary":"ok","flags":[]}'),
        _wiki(tmp_path),
        "topic",
        "# Topic\n\nSee [[missing-page]].\n",
        scope="personal",
    )
    assert "broken_links" in o.flags


def test_outline_pii_only_when_leaving_machine(tmp_path):
    from anthill.wiki.review import outline_change

    be, body = _FakeBackend('{"summary":"ok","flags":[]}'), "# C\n\nEmail jane.doe@example.com.\n"
    assert "pii" in outline_change(be, _wiki(tmp_path), "c", body, scope="org").flags
    assert "pii" not in outline_change(be, _wiki(tmp_path), "c2", body, scope="personal").flags


def test_gate_autoapply_vs_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    from anthill.web.db import WikiReview

    _client, app_mod, ids = _app_client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        # clean -> auto-applied, no review row, page on disk
        monkeypatch.setattr(
            app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend('{"summary":"ok","flags":[]}')
        )
        applied = app_mod.propose_wiki_write(
            s,
            org_id=ids["org"],
            proposed_by=ids["owner"],
            slug="alpha",
            content="# Alpha\n\nA fact.\n",
            target_scope="org",
        )
        s.commit()
        assert applied is True
        assert s.query(WikiReview).count() == 0
        assert (tmp_path / "org" / "wiki" / "alpha.md").exists()

        # flagged -> queued, no page written
        monkeypatch.setattr(
            app_mod,
            "_backend_from_cfg",
            lambda cfg: _FakeBackend('{"summary":"dupe","flags":["duplicate"]}'),
        )
        applied2 = app_mod.propose_wiki_write(
            s,
            org_id=ids["org"],
            proposed_by=ids["owner"],
            slug="beta",
            content="# Beta\n\nSomething.\n",
            target_scope="org",
        )
        s.commit()
        assert applied2 is False
        row = s.query(WikiReview).filter_by(slug="beta", status="pending").one()
        assert "duplicate" in row.flags and row.target_scope == "org"
        assert not (tmp_path / "org" / "wiki" / "beta.md").exists()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


# ── Step 6: read-side scoping ───────────────────────────────────────────────────


def test_recall_memory_is_team_aware(tmp_path, monkeypatch):
    import anthill.memory as mem

    monkeypatch.setattr(mem, "embed_text", lambda q: None)  # force keyword recall
    from anthill.web.recall import recall_memory

    db_ = _session(tmp_path)
    from anthill.web.db import Team

    db_.add(
        Team(id=7, org_id=1, name="Seven", slug="seven", owner_id=1)
    )  # team_id=7 referenced below
    db_.flush()
    db_.add_all(
        [
            MemoryItem(org_id=1, user_id=1, scope="personal", text="project deadline mine"),
            MemoryItem(org_id=1, user_id=2, scope="personal", text="project deadline other user"),
            MemoryItem(
                org_id=1, user_id=2, scope="team", team_id=7, text="project deadline team seven"
            ),
            MemoryItem(org_id=1, user_id=None, scope="org", text="project deadline org wide"),
        ]
    )
    db_.commit()

    with_team = recall_memory(db_, 1, 1, "project deadline", k=10, team_ids=[7])
    assert "mine" in with_team and "team seven" in with_team and "org wide" in with_team
    assert "other user" not in with_team  # another user's personal memory is private

    without_team = recall_memory(db_, 1, 1, "project deadline", k=10, team_ids=[])
    assert "mine" in without_team and "team seven" not in without_team


def test_merge_relevant_blends_workspaces(tmp_path, monkeypatch):
    import anthill.wiki.ask as ask_mod
    from anthill.wiki.workspace import Workspace

    def _boom(*a, **k):
        raise RuntimeError("no embedder")

    monkeypatch.setattr(ask_mod.emb, "embed", _boom)  # force keyword retrieval

    personal = Workspace(tmp_path / "p")
    personal.init()
    personal.write_page("alpha-personal", "# Alpha personal\n\nalpha fact one.\n")
    personal.rebuild_index()
    team = Workspace(tmp_path / "t")
    team.init()
    team.write_page("alpha-team", "# Alpha team\n\nalpha fact two.\n")
    team.rebuild_index()

    stems = {p.stem for p in ask_mod._merge_relevant([personal, team], "alpha", 5)}
    assert "alpha-personal" in stems and "alpha-team" in stems  # both wikis blended


# ── render smoke (templates compile) ────────────────────────────────────────────


def test_team_and_review_pages_render(tmp_path):
    from anthill.web.db import User

    client, app_mod, ids = _app_client(tmp_path)
    try:
        _auth(client, app_mod, ids["owner"], ids["org"], role="member")
        assert client.get("/teams").status_code == 200  # teams.html
        # Role is authoritative from the DB now (a token can't claim admin for a member user), so
        # promote the user in the DB before exercising the admin-only review page.
        s = app_mod._SessionFactory()
        s.query(User).filter(User.id == ids["owner"]).update({User.role: "admin"})
        s.commit()
        s.close()
        _auth(client, app_mod, ids["owner"], ids["org"], role="admin")
        assert client.get("/wiki/review").status_code == 200  # wiki_review.html
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_project_home_shows_its_tasks_and_agents(tmp_path):
    # #419 P2: a project home reads as "chats + tasks + agents + wiki". A task and an agent scoped to the
    # project (team_id) appear on /teams/{id}, alongside the "in this project" create affordances.
    from anthill.web.db import Agent, ScheduledTask, Team, TeamMembership

    client, app_mod, ids = _app_client(tmp_path)
    try:
        _auth(client, app_mod, ids["owner"], ids["org"], role="member")
        s = app_mod._SessionFactory()
        team = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["owner"])
        s.add(team)
        s.flush()
        s.add(TeamMembership(team_id=team.id, user_id=ids["owner"], role="owner", status="active"))
        s.add(
            ScheduledTask(
                org_id=ids["org"],
                created_by=ids["owner"],
                title="Weekly digest",
                goal="summarise the week",
                plane="team",
                team_id=team.id,
            )
        )
        s.add(
            Agent(
                org_id=ids["org"],
                created_by=ids["owner"],
                name="Research scout",
                mandate="track competitors",
                plane="team",
                team_id=team.id,
            )
        )
        s.commit()
        tid = team.id
        s.close()
        body = client.get(f"/teams/{tid}").text
        assert "Tasks in this project" in body and "Weekly digest" in body
        assert "Agents in this project" in body and "Research scout" in body
        assert f"/agents?project={tid}" in body  # the in-project agent create affordance
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_projects_page_frames_solo_vs_org(tmp_path):
    # #419 P3 Solo framing: an org account frames a project as a shared team space you invite people to;
    # a Solo account (no org backend) frames it as a personal, always-local, single-user project.
    from anthill.web.db import OrgSettings

    client, app_mod, ids = _app_client(tmp_path)
    try:
        _auth(client, app_mod, ids["owner"], ids["org"], role="member")
        # _app_client configures a validated org backend -> org framing
        body = client.get("/teams").text
        assert "invite existing org members" in body
        assert "always local" not in body
        # Flip to Solo (no org backend configured) -> personal-project framing
        s = app_mod._SessionFactory()
        s.query(OrgSettings).filter(OrgSettings.org_id == ids["org"]).update(
            {OrgSettings.org_backend_status: ""}
        )
        s.commit()
        s.close()
        body = client.get("/teams").text
        assert "always local" in body and "single-user" in body
        assert "invite existing org members" not in body
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_project_home_shares_tasks_agents_but_not_chats(tmp_path):
    # #419 shared visibility: tasks + agents are shared team infrastructure, so every project member sees
    # them and can open another member's project agent read-only; chats stay per-member (personal).
    from anthill.web.db import Agent, Conversation, ScheduledTask, Team, TeamMembership

    client, app_mod, ids = _app_client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        team = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["owner"])
        s.add(team)
        s.flush()
        s.add(TeamMembership(team_id=team.id, user_id=ids["owner"], role="owner", status="active"))
        s.add(
            TeamMembership(team_id=team.id, user_id=ids["member"], role="member", status="active")
        )
        # all created by the OWNER; the MEMBER views them
        s.add(
            ScheduledTask(
                org_id=ids["org"],
                created_by=ids["owner"],
                title="Owner digest",
                goal="x",
                plane="team",
                team_id=team.id,
            )
        )
        s.add(
            Agent(
                org_id=ids["org"],
                created_by=ids["owner"],
                name="Owner scout",
                mandate="x",
                plane="team",
                team_id=team.id,
            )
        )
        s.add(
            Conversation(
                org_id=ids["org"],
                user_id=ids["owner"],
                title="Owner private chat",
                plane="team",
                team_id=team.id,
            )
        )
        s.commit()
        tid = team.id
        agent_id = s.query(Agent).filter(Agent.team_id == tid).first().id
        s.close()
        _auth(client, app_mod, ids["member"], ids["org"], role="member")
        body = client.get(f"/teams/{tid}").text
        assert "Owner digest" in body  # shared task visible to a fellow member
        assert "Owner scout" in body  # shared agent visible to a fellow member
        assert "Owner private chat" not in body  # chats stay per-member
        # a fellow member can open the shared agent read-only (not redirected to /agents)
        assert client.get(f"/agents/{agent_id}", follow_redirects=False).status_code == 200
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_project_member_can_operate_but_not_delete_a_shared_agent(tmp_path):
    # #419: a project's agents are shared team infrastructure - a fellow member may run/pause them, but
    # editing and deleting stay with the creator (or an org admin).
    from anthill.web.db import Agent, Team, TeamMembership

    client, app_mod, ids = _app_client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        team = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["owner"])
        s.add(team)
        s.flush()
        s.add(TeamMembership(team_id=team.id, user_id=ids["owner"], role="owner", status="active"))
        s.add(
            TeamMembership(team_id=team.id, user_id=ids["member"], role="member", status="active")
        )
        s.add(
            Agent(
                org_id=ids["org"],
                created_by=ids["owner"],
                name="Scout",
                mandate="x",
                plane="team",
                team_id=team.id,
                status="paused",
            )
        )
        s.commit()
        aid = s.query(Agent).filter(Agent.team_id == team.id).first().id
        s.close()
        # the MEMBER (not the creator) operates the shared agent
        _auth(client, app_mod, ids["member"], ids["org"], role="member")
        # run-now is allowed -> the agent becomes active and due
        assert client.post(f"/agents/{aid}/run-now", follow_redirects=False).status_code == 302
        s = app_mod._SessionFactory()
        a = s.query(Agent).filter(Agent.id == aid).one()
        assert a.status == "active" and a.next_run_at is not None
        s.close()
        # delete is NOT allowed for a fellow member (creator/admin only) -> the agent still exists
        client.post(f"/agents/{aid}/delete", follow_redirects=False)
        s = app_mod._SessionFactory()
        assert s.query(Agent).filter(Agent.id == aid).first() is not None
        s.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_team_invite_notifies_the_invited_member(tmp_path):
    # #284 event routing: inviting an org member to a project creates an in-app notification for them.
    from anthill.web.db import Notification

    client, app_mod, ids = _app_client(tmp_path)
    try:
        _auth(client, app_mod, ids["owner"], ids["org"])
        r = client.post("/teams", data={"name": "Platform"}, follow_redirects=False)
        team_id = int(r.headers["location"].rsplit("/", 1)[1])
        client.post(
            f"/teams/{team_id}/invite", data={"email": "member@acme.com"}, follow_redirects=False
        )
        s = app_mod._SessionFactory()
        n = s.query(Notification).filter_by(user_id=ids["member"], kind="invite").first()
        assert n is not None and "Platform" in n.title and n.link == "/teams"
        s.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
