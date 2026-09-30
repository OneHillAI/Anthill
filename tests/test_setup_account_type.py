"""Phase 6b1: the post-signup Solo-vs-organization branch screen.

Solo and organization accounts get identical model/council access - the only thing choosing
"organization" adds is an org name and, later, the ability to invite people. This screen is shown
exactly once per fresh account (gated on OrgSettings.account_type_chosen, mirroring
local_model_chosen's own gate-until-chosen pattern) and never re-shown to an existing account.

account_type_chosen defaults to True everywhere EXCEPT a genuinely fresh /setup POST - a test fixture
that constructs OrgSettings() directly (as most of this test suite does) must never be unexpectedly
routed through this screen, so the default is deliberately the non-disruptive one.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, account_type_chosen=False)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_fresh_account_is_redirected_to_the_branch_screen(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/setup/account-type"


def test_branch_screen_renders_both_choices(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/setup/account-type").text
    assert "Continue solo" in body
    assert "Set up an organization" in body
    assert 'name="org_name"' in body


def test_choosing_solo_marks_it_chosen_and_keeps_solo_topology(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    r = c.post("/setup/account-type", data={"account_type": "solo"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.account_type_chosen is True
    assert cfg.deployment_topology == "solo"


def test_choosing_org_sets_topology_and_name(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    r = c.post(
        "/setup/account-type",
        data={"account_type": "org", "org_name": "Acme Corp"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/"
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    org = s.query(Organization).first()
    assert cfg.account_type_chosen is True
    assert cfg.deployment_topology == "org"
    assert org.name == "Acme Corp"


def test_choosing_org_without_a_name_does_not_clobber_the_placeholder_name(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post("/setup/account-type", data={"account_type": "org", "org_name": ""})
    org = app_mod._SessionFactory().query(Organization).first()
    assert org.name == "A"  # unchanged - blank org_name is not an error, just deferred


def test_already_chosen_account_is_never_shown_the_branch_screen_again(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    c.post("/setup/account-type", data={"account_type": "solo"})
    r = c.get("/setup/account-type", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_a_default_constructed_orgsettings_never_redirects_existing_fixtures(tmp_path, monkeypatch):
    # The safety net this whole design depends on: dozens of existing test fixtures across this suite
    # construct OrgSettings() directly without setting account_type_chosen. None of them must be
    # newly redirected to the branch screen by this change.
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w2"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o2"))
    eng = create_engine(f"sqlite:///{tmp_path / 'b.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="B", slug="b")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@b.com", role="admin", active=True)
    s.add_all(
        [u, OrgSettings(org_id=o.id)]
    )  # no account_type_chosen set, exactly like existing fixtures
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))

    r = c.get("/", follow_redirects=False)
    assert r.headers.get("location") != "/setup/account-type"
