"""Profile awareness + workspace-focused nav: the rail always shows which device profile you are in
and lets you switch; a /profile hub gathers account + profile; and on workspace pages (chat/tasks/
agents) the lower nav groups collapse. Model-free."""

import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


class _Req:
    """Minimal stand-in for the bits of Request that _nav_context reads."""

    def __init__(self, path, token=None):
        self.url = types.SimpleNamespace(path=path)
        self.cookies = {"session_token": token} if token else {}
        self.state = types.SimpleNamespace()


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    # Isolate the device-profile registry to a temp dir and seed Personal (default) + Work.
    pbase = tmp_path / "profiles"
    monkeypatch.setenv("ANTHILL_PROFILES_BASE", str(pbase))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    from anthill import profiles as pm

    pm.migrate_or_init(pbase)  # default profile, named "Personal" on a fresh base
    pm.create_profile(pbase, "Work", "#059669")

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
    u = User(org_id=org.id, email="ada@acme.com", role="admin", active=True)
    s.add(u)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return app_mod, client, {"org": org.id, "u": u.id}


# ── workspace_mode: only chat / tasks / agents ──────────────────────────────────


def test_workspace_mode_only_in_workspace(tmp_path, monkeypatch):
    app_mod, _, _ = _app(tmp_path, monkeypatch)
    for p in ("/chat", "/chat/12", "/tasks", "/tasks/3/result", "/agents", "/agents/7"):
        assert app_mod._nav_context(_Req(p))["workspace_mode"] is True, p
    for p in ("/", "/wiki", "/memory", "/agent-access", "/settings"):
        assert app_mod._nav_context(_Req(p))["workspace_mode"] is False, p


# ── the rail always knows which profile it is ───────────────────────────────────


def test_nav_context_exposes_current_profile_after_live_authentication(tmp_path, monkeypatch):
    app_mod, client, _ = _app(tmp_path, monkeypatch)
    ctx = app_mod._nav_context(_Req("/", client.cookies.get("session_token")))
    assert ctx["nav_profile"]["name"] == "Personal"
    assert {p["name"] for p in ctx["nav_profiles"]} == {"Personal", "Work"}
    assert ctx["nav_profile"]["colour"].startswith("#")


def test_revoked_session_hides_device_profiles_on_public_pages(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    authenticated = BeautifulSoup(client.get("/docs/how-it-works").text, "html.parser")
    assert authenticated.select_one("button.profile-chip") is not None

    from anthill.web.db import User

    session = app_mod._SessionFactory()
    session.query(User).filter(User.id == ids["u"]).update({User.active: False})
    session.commit()

    revoked = BeautifulSoup(client.get("/docs/how-it-works").text, "html.parser")
    assert revoked.select_one("button.profile-chip") is None


def test_dashboard_shows_chip_no_fold_workspace_folds(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    home = client.get("/").text
    assert "profile-chip" in home and "Personal" in home  # the always-visible indicator
    assert "workspace-mode" not in home  # dashboard is not a workspace -> groups not folded
    tasks = client.get("/tasks").text
    assert (
        "workspace-mode" in tasks and "nav-more" in tasks
    )  # groups collapse into "More" in a workspace


# ── the /profile hub gathers account + profile ──────────────────────────────────


def test_profile_hub_renders(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    page = client.get("/profile").text
    assert "This profile" in page and "Personal" in page  # the device profile
    assert "ada@acme.com" in page and "Your account" in page  # the account
    assert "/personalize" in page  # jump to personalization


def test_account_name_updates_and_lands_on_hub(tmp_path, monkeypatch):
    app_mod, client, ids = _app(tmp_path, monkeypatch)
    r = client.post("/account/name", data={"display_name": "Ada Lovelace"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/profile?saved=name"
    from anthill.web.db import User

    me = app_mod._SessionFactory().query(User).filter(User.id == ids["u"]).first()
    assert me.display_name == "Ada Lovelace"
    assert "Ada Lovelace" in client.get("/profile").text


def test_password_change_honours_next_url(tmp_path, monkeypatch):
    # A fresh account has no password yet, so no current-password is required to set one.
    _, client, _ = _app(tmp_path, monkeypatch)
    r = client.post(
        "/account/password",
        data={"new_password": "correcthorse1", "next_url": "/profile"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/profile?changed=1"


def test_password_next_url_rejects_external(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    r = client.post(
        "/account/password",
        data={"new_password": "correcthorse1", "next_url": "//evil.example"},
        follow_redirects=False,
    )
    # An off-site next is ignored; it falls back to the account page.
    assert r.headers["location"] == "/account?changed=1"
