"""Org name is optional at setup (P3): a blank name derives a friendly default from the admin email
domain, and the org can be named (or renamed) later on the Organization settings page."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _fresh_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app), app_mod


def test_solo_signup_names_the_workspace_personal(tmp_path, monkeypatch):
    # A solo sign-up is a personal workspace (a tenant of one): it gets a NEUTRAL name, never one derived
    # from the email domain, so no identifying "org" chrome leaks (#481 reframed B).
    client, app_mod = _fresh_app(tmp_path, monkeypatch)
    r = client.post(
        "/setup",
        data={"admin_email": "boss@acme.com", "admin_password": "longenough123"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    org = app_mod._SessionFactory().query(Organization).first()
    assert org is not None and org.name == "Personal" and org.slug  # neutral, not "Acme"


def test_explicit_org_signup_derives_name_from_email_domain(tmp_path, monkeypatch):
    # Only an explicit org sign-up (topology=org) derives a friendly name from the email domain.
    client, app_mod = _fresh_app(tmp_path, monkeypatch)
    client.post(
        "/setup",
        data={
            "org_name": "",
            "admin_email": "boss@acme.com",
            "admin_password": "longenough123",
            "topology": "org",
        },
        follow_redirects=False,
    )
    assert app_mod._SessionFactory().query(Organization).first().name == "Acme"


def test_org_signup_without_email_domain_uses_placeholder(tmp_path, monkeypatch):
    client, app_mod = _fresh_app(tmp_path, monkeypatch)
    client.post(
        "/setup",
        data={"admin_email": "noatsign", "admin_password": "longenough123", "topology": "org"},
        follow_redirects=False,
    )
    org = app_mod._SessionFactory().query(Organization).first()
    assert org.name == "My organization"


def test_setup_keeps_an_explicit_name(tmp_path, monkeypatch):
    client, app_mod = _fresh_app(tmp_path, monkeypatch)
    client.post(
        "/setup",
        data={
            "org_name": "Globex Inc",
            "admin_email": "a@acme.com",
            "admin_password": "longenough123",
        },
        follow_redirects=False,
    )
    assert app_mod._SessionFactory().query(Organization).first().name == "Globex Inc"


# ── rename later on the Organization page ────────────────────────────────────────────


def _seeded_admin(tmp_path, monkeypatch):
    client, app_mod = _fresh_app(tmp_path, monkeypatch)
    from anthill.web.crypto import make_token

    s = app_mod._SessionFactory()
    org = Organization(name="My organization", slug="my-organization")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add_all([admin, OrgSettings(org_id=org.id)])
    s.commit()
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, app_mod, org.id


def test_org_can_be_named_later(tmp_path, monkeypatch):
    client, app_mod, org_id = _seeded_admin(tmp_path, monkeypatch)
    r = client.post(
        "/settings/organization/name", data={"org_name": "Acme Corp"}, follow_redirects=False
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    org = app_mod._SessionFactory().query(Organization).filter(Organization.id == org_id).first()
    assert org.name == "Acme Corp" and org.slug == "acme-corp"


def test_blank_rename_is_rejected(tmp_path, monkeypatch):
    client, app_mod, org_id = _seeded_admin(tmp_path, monkeypatch)
    r = client.post("/settings/organization/name", data={"org_name": "  "}, follow_redirects=False)
    assert r.status_code == 302 and "error=name" in r.headers["location"]
    org = app_mod._SessionFactory().query(Organization).filter(Organization.id == org_id).first()
    assert org.name == "My organization"  # unchanged


def test_name_field_shown_on_org_page(tmp_path, monkeypatch):
    client, _, _ = _seeded_admin(tmp_path, monkeypatch)
    # the org name form lives on Organization -> General (/settings/general)
    page = client.get("/settings/general").text
    assert "/settings/organization/name" in page and 'name="org_name"' in page


def test_personal_workspace_shows_no_org_chrome_in_nav(tmp_path, monkeypatch):
    # A personal workspace (no shared backend configured) is a tenant of one: the nav must NOT show the
    # workspace name as org chrome until the user creates a real organization (#481 reframed B).
    client, _app_mod, _org_id = _seeded_admin(tmp_path, monkeypatch)
    page = client.get("/").text
    assert 'class="brand-org"' not in page  # no org name chrome for a solo user
