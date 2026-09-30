"""The auth front door.

A freshly-downloaded app must not dead-end, and it must not show two welcomes. On first run
(no account yet) /login sends the user straight to /setup - a single welcome that offers a solo
account or an organization - rather than a redundant "set up your organization" screen. Once an
account exists, /login is the normal sign-in.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app)


def test_login_redirects_to_setup_on_fresh_install(tmp_path, monkeypatch):
    # No account yet: /login must not render a second "set up your organization" welcome -
    # it sends the user to the single /setup front door.
    client = _client(tmp_path, monkeypatch)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/setup"
    assert 'href="/login"' not in client.get("/login").text


def test_setup_is_the_single_first_run_welcome_personal_no_org(tmp_path, monkeypatch):
    # The one first-run screen creates a PERSONAL account with no organization setup required
    # (docs/specs/signup-no-org.md). It must not offer sign-in when this install has no account.
    client = _client(tmp_path, monkeypatch)
    r = client.get("/setup")
    assert r.status_code == 200
    assert (
        'name="admin_email"' in r.text and 'name="topology"' not in r.text
    )  # personal, no org chooser
    assert 'href="/login"' not in r.text


def test_setup_offers_signin_after_account_creation(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    created = client.post(
        "/setup",
        data={"admin_email": "owner@example.com", "admin_password": "correct horse battery"},
        follow_redirects=False,
    )
    assert created.status_code == 302

    client.cookies.clear()  # Return to the same installation as a logged-out user.
    setup = client.get("/setup")
    assert setup.status_code == 200
    assert 'href="/login"' in setup.text

    login = client.get("/login")
    assert login.status_code == 200
    assert 'action="/login"' in login.text
