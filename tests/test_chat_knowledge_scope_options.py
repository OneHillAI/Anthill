"""Chat's Options -> knowledge-scope dropdown must only offer scopes that actually apply to this
account, instead of always showing all four ("All knowledge", "Personal only", "My teams", "Org
wiki") regardless of context.

- "Org wiki" only means something once an org backend has ever been configured
  (planes.is_org_mode) - a Solo account has no org wiki at all, so showing it is misleading.
- "My teams" only means something once the user actually belongs to at least one; with exactly one
  team, the generic plural reads oddly - the option should name the team directly instead.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, org.id, user.id


def _mkconv(app_mod, org_id, user_id, *, plane="solo"):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = Conversation(org_id=org_id, user_id=user_id, title="Chat", plane=plane)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def _make_team(app_mod, org_id, owner_id, name):
    from anthill.web.db import Team, TeamMembership

    s = app_mod._SessionFactory()
    try:
        t = Team(org_id=org_id, name=name, slug=name.lower().replace(" ", "-"), owner_id=owner_id)
        s.add(t)
        s.flush()
        s.add(TeamMembership(team_id=t.id, user_id=owner_id, role="owner", status="active"))
        s.commit()
        return t.id
    finally:
        s.close()


def test_solo_no_teams_shows_only_all_and_personal(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id)
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert 'value="all"' in r.text and 'value="personal"' in r.text
    assert 'value="team"' not in r.text  # no teams -> no "My teams" option
    assert 'value="org"' not in r.text  # solo -> no org wiki


def test_one_team_shows_its_real_name_not_generic_my_teams(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _make_team(app_mod, org_id, user_id, "Rocket Launch")
    cid = _mkconv(app_mod, org_id, user_id)
    r = client.get(f"/chat/{cid}")
    assert '<option value="team">Rocket Launch</option>' in r.text
    assert "My teams" not in r.text


def test_two_teams_falls_back_to_the_generic_my_teams_label(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _make_team(app_mod, org_id, user_id, "Rocket Launch")
    _make_team(app_mod, org_id, user_id, "Moon Base")
    cid = _mkconv(app_mod, org_id, user_id)
    r = client.get(f"/chat/{cid}")
    assert '<option value="team">My teams</option>' in r.text


def test_org_mode_account_shows_org_wiki_option(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(
        OrgSettings.org_id == org_id
    ).first().org_backend_status = "validated"
    s.commit()
    cid = _mkconv(app_mod, org_id, user_id)
    r = client.get(f"/chat/{cid}")
    assert '<option value="org">Org wiki</option>' in r.text


def test_solo_with_a_team_but_no_org_shows_team_but_not_org_wiki(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _make_team(app_mod, org_id, user_id, "Rocket Launch")
    cid = _mkconv(app_mod, org_id, user_id)
    r = client.get(f"/chat/{cid}")
    assert 'value="team"' in r.text
    assert 'value="org"' not in r.text
