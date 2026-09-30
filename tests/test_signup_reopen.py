"""Sign-up stays open past the very first account.

Before this fix, /setup (GET+POST) redirected away the instant any Organization row existed
anywhere - so only the first-ever visitor to an install could ever create an account, and every
returning visitor who logged out saw a sign-in-only screen with no way back to sign-up (login.html
still said "ask your admin to invite you", stale copy from before #481 made sign-up self-serve).
This covers: /setup staying reachable for a second/third personal account, the login.html link
back to it, the duplicate-email guard that reachability now needs, and OAuth sign-up buttons
staying hidden past the first account (oauth_login_outcome only self-registers the first account;
showing the buttons on a page a second visitor can reach would dead-end them).
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import Organization, OrgSettings, User


def _fresh_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app), app_mod


def _with_one_account(app_mod):
    s = app_mod._SessionFactory()
    org = Organization(name="Personal", slug="personal")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="solo"))
    user = User(
        org_id=org.id,
        email="first@example.com",
        display_name="First",
        hashed_password="x",
        role="admin",
        active=True,
    )
    s.add(user)
    s.commit()
    return org.id, user.id  # ids only - the session closes when this function returns


def test_setup_get_stays_reachable_once_an_account_already_exists(tmp_path, monkeypatch):
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    _with_one_account(app_mod)
    r = c.get("/setup")
    assert r.status_code == 200
    assert 'name="admin_email"' in r.text


def test_setup_post_creates_an_additional_account(tmp_path, monkeypatch):
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    _with_one_account(app_mod)
    r = c.post(
        "/setup",
        data={"admin_email": "second@example.com", "admin_password": "longenoughpw1"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    users = app_mod._SessionFactory().query(User).all()
    assert {u.email for u in users} == {"first@example.com", "second@example.com"}
    orgs = app_mod._SessionFactory().query(Organization).all()
    assert len(orgs) == 2  # each personal account keeps its own hidden compat org


def test_setup_post_rejects_a_duplicate_email_without_500(tmp_path, monkeypatch):
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    _with_one_account(app_mod)
    r = c.post(
        "/setup",
        data={"admin_email": "First@Example.com", "admin_password": "longenoughpw1"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already exists" in r.text
    assert app_mod._SessionFactory().query(User).count() == 1  # no duplicate row created


def test_setup_get_redirects_a_logged_in_visitor_away(tmp_path, monkeypatch):
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    org_id, user_id = _with_one_account(app_mod)
    c.cookies.set("session_token", make_token(user_id, org_id, "admin"))
    r = c.get("/setup", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_page_links_to_signup(tmp_path, monkeypatch):
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    _with_one_account(app_mod)
    r = c.get("/login")
    assert r.status_code == 200
    assert 'href="/setup"' in r.text
    assert "ask your admin" not in r.text.lower()


def test_oauth_signup_buttons_hidden_once_an_account_already_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    c, app_mod = _fresh_app(tmp_path, monkeypatch)
    _with_one_account(app_mod)
    r = c.get("/setup")
    assert r.status_code == 200
    assert "Sign up with Google" not in r.text
    assert 'name="admin_email"' in r.text  # the password form itself is still there


def test_oauth_signup_buttons_shown_on_a_genuine_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    c, _app_mod = _fresh_app(tmp_path, monkeypatch)
    r = c.get("/setup")
    assert r.status_code == 200
    assert "Sign up with Google" in r.text
