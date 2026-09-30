"""Org settings consolidation (P1): one hub at /settings/org gathers every org-scoped setting - cloud
model, wiki, skills, tuning, users, projects, connectors, agent access, and the previously-orphan infra
routes (backend/backup/appliance/remote/events). Admin-only; the two org rail items collapse to one."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _clients(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member, OrgSettings(org_id=org.id)])
    s.commit()
    ac = TestClient(app_mod.app)
    ac.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    mc = TestClient(app_mod.app)
    mc.cookies.set("session_token", make_token(member.id, org.id, "member"))
    return ac, mc


def test_org_hub_gathers_every_org_setting(tmp_path, monkeypatch):
    ac, _mc = _clients(tmp_path, monkeypatch)
    body = ac.get("/settings/org").text
    assert (
        "Manage Acme" in body
    )  # the clean Manage-Org view (mockup header), not the old grid title
    # tabbed now (Members / Model & compute / Knowledge / Integrations / Metrics), but every org-scoped
    # surface stays reachable from this one place - incl. org skills + the ex-orphan infra routes
    for tab in (">Members<", ">Knowledge<", ">Integrations<", ">Metrics<"):
        assert tab in body, tab
    for href in (
        "/settings/organization",
        "/skills",
        "/training",
        "/users",
        "/teams",
        "/connectors/mcp",
        "/agent-access",
        "/backend",
        "/backup",
        "/settings/appliance",
        "/settings/remote",
        "/settings/events",
    ):
        assert href in body, href
    assert "Org skills" in body  # the founder ask: orgs have their own skills too
    # Cloud & model and Org wiki must reach DISTINCT tabs (base = Model tab; wiki = its own sub-page),
    # not the same default destination (ASDD review of #462).
    assert "/settings/organization/wiki" in body  # the Org wiki card's distinct destination


def test_org_hub_is_admin_only(tmp_path, monkeypatch):
    ac, mc = _clients(tmp_path, monkeypatch)
    assert ac.get("/settings/org").status_code == 200
    assert mc.get("/settings/org").status_code in (
        302,
        303,
        403,
    )  # members can't reach org settings


def test_nav_collapses_to_one_org_settings_entry(tmp_path, monkeypatch):
    ac, _mc = _clients(tmp_path, monkeypatch)
    nav = ac.get("/chat").text
    assert ">Settings</a>" in nav  # the single Settings entry (Org settings folds under it)...
    assert ">Org settings</a>" not in nav  # ...no separate Org settings rail item
    assert ">Cloud &amp; model</a>" not in nav  # ...and no old "Cloud & model" rail item
    assert (
        ">Organization</a>" not in nav
    )  # no duplicate "Organization" rail link (the rail is flat now)
