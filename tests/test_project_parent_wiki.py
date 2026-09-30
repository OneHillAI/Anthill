"""Connect-a-project-to-its-parent-wiki (#419, docs/specs/project-parent-wiki-connect.md): a project
chooses at setup whether to also read its parent wiki (org, or personal in Solo). Default connected;
disconnecting isolates the project's READ grounding without changing its write target. Model-free."""

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


def _project(s, *, connect):
    from anthill.web.db import Organization, Team, TeamMembership, User

    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com", role="admin", active=True)
    s.add(u)
    s.flush()
    t = Team(org_id=org.id, name="P", slug="p", owner_id=u.id, connect_parent_wiki=connect)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=u.id, role="owner", status="active"))
    s.commit()
    return org, u, t


def _tiers(s, u, t, is_org):
    from anthill.web.agent_context import context_workspaces

    scoped = context_workspaces(s, user_id=u.id, plane="team", team_id=t.id, is_org=is_org)
    return [tier for tier, _label, _ws in scoped]


def test_connected_org_project_blends_the_org_wiki(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=True)
    tiers = _tiers(s, u, t, is_org=True)
    assert "team" in tiers and "org" in tiers  # own wiki + the connected parent (org)


def test_disconnected_org_project_is_isolated(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=False)
    tiers = _tiers(s, u, t, is_org=True)
    assert (
        "team" in tiers and "org" not in tiers
    )  # own wiki only; the parent org wiki is NOT blended


def test_disconnected_solo_project_drops_the_personal_wiki(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=False)
    tiers = _tiers(s, u, t, is_org=False)  # a Solo (local) project
    assert "team" in tiers and "personal" not in tiers  # isolated from the personal parent too


def test_connected_solo_project_keeps_the_personal_wiki(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=True)
    tiers = _tiers(s, u, t, is_org=False)
    assert "team" in tiers and "personal" in tiers


def test_disconnect_never_changes_the_write_target(tmp_path, monkeypatch):
    # the connect choice is read-grounding only: even a disconnected project WRITES to its own wiki.
    from anthill.web.agent_context import run_wiki_workspace
    from anthill.web.plane_routing import PlaneInference

    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=False)
    pi = PlaneInference(
        plane="team",
        backend="ollama",
        base_url="x",
        model="m",
        api_key=None,
        wiki_scope="team",
        use_personal_context=True,
    )
    ws = run_wiki_workspace(s, plane_inf=pi, team_id=t.id, member_user_id=u.id)
    assert ws.replace("\\", "/").endswith(f"team-{t.id}")


def test_create_and_toggle_the_connect_flag(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, Team, User

    s = _session(tmp_path, monkeypatch)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="a@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, org.id, "admin"))

    # a legacy/API caller that OMITS the field entirely -> keeps the connected default (R1)
    c.post("/teams", data={"name": "Legacy"}, follow_redirects=False)
    legacy = app_mod._SessionFactory().query(Team).filter(Team.slug == "legacy").first()
    assert legacy.connect_parent_wiki is True
    # the create FORM (sends the cpw_submitted sentinel) with the box UNCHECKED -> isolate
    c.post("/teams", data={"name": "Iso", "cpw_submitted": "1"}, follow_redirects=False)
    iso = app_mod._SessionFactory().query(Team).filter(Team.slug == "iso").first()
    assert iso.connect_parent_wiki is False
    # the form with the box checked -> connected
    c.post(
        "/teams",
        data={"name": "Conn", "cpw_submitted": "1", "connect_parent_wiki": "on"},
        follow_redirects=False,
    )
    conn = app_mod._SessionFactory().query(Team).filter(Team.slug == "conn").first()
    assert conn.connect_parent_wiki is True
    # the owner toggles it later from settings
    c.post(
        f"/teams/{iso.id}/wiki-connect", data={"connect_parent_wiki": "on"}, follow_redirects=False
    )
    assert (
        app_mod._SessionFactory().query(Team).filter(Team.id == iso.id).first().connect_parent_wiki
        is True
    )


def test_project_read_extras_blends_only_the_connected_parent(tmp_path, monkeypatch):
    # the chat read-side helper (used by extra_ws) - mirrors context_workspaces, so testing it covers the
    # chat blend behaviour for both project types + states without running the chat stream.
    from anthill.web.agent_context import project_read_extras

    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=True)
    # connected org project (cloud, use_personal_context False) -> blend the org wiki read-only
    org_extras = project_read_extras(
        s, team_id=t.id, use_personal_context=False, wiki_scope="all", user_id=u.id
    )
    assert [w.scope for w in org_extras] == ["org"]
    # connected Solo project (local, use_personal_context True) -> blend the personal wiki
    solo_extras = project_read_extras(
        s, team_id=t.id, use_personal_context=True, wiki_scope="all", user_id=u.id
    )
    assert [w.scope for w in solo_extras] == ["personal"]
    # the scope filter is honoured (a Solo project under wiki_scope=org blends nothing)
    assert (
        project_read_extras(
            s, team_id=t.id, use_personal_context=True, wiki_scope="org", user_id=u.id
        )
        == []
    )


def test_existing_projects_migrate_to_connected(tmp_path):
    # Backward compatibility (#469 review): ensure_columns adds connect_parent_wiki with DEFAULT 1, so a
    # pre-existing project (created before the column existed) reads as CONNECTED, not NULL/disconnected.
    from sqlalchemy import create_engine, text

    from anthill.web import migrate

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE teams (id INTEGER PRIMARY KEY, org_id INTEGER, name VARCHAR, "
                "slug VARCHAR, owner_id INTEGER, created_at DATETIME)"
            )
        )
        c.execute(text("INSERT INTO teams (id, name, slug, owner_id) VALUES (1, 'Old', 'old', 1)"))
    added = migrate.ensure_columns(eng)
    assert "teams.connect_parent_wiki" in added
    with eng.begin() as c:
        val = c.execute(text("SELECT connect_parent_wiki FROM teams WHERE id=1")).scalar()
    assert val in (1, True)  # existing project -> connected, never NULL/disconnected


def test_project_read_extras_disconnected_blends_nothing(tmp_path, monkeypatch):
    from anthill.web.agent_context import project_read_extras

    s = _session(tmp_path, monkeypatch)
    _org, u, t = _project(s, connect=False)
    assert (
        project_read_extras(
            s, team_id=t.id, use_personal_context=False, wiki_scope="all", user_id=u.id
        )
        == []
    )
    assert (
        project_read_extras(
            s, team_id=t.id, use_personal_context=True, wiki_scope="all", user_id=u.id
        )
        == []
    )
