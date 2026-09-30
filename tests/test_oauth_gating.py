"""Google (OAuth) login is invited-users-only: no open signup into the org."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web.app import oauth_login_outcome
from anthill.web.db import Organization, User, create_tables


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()
    s.add(Organization(name="Acme", slug="acme"))
    s.commit()
    return s


def test_unknown_email_is_rejected(db):
    u, err = oauth_login_outcome(db, "stranger@gmail.com", "sub-1", "Stranger")
    assert u is None and err == "not_invited"
    # and no account was created for the stranger
    assert db.query(User).filter(User.email == "stranger@gmail.com").first() is None


def test_invited_pending_user_is_activated_and_linked(db):
    org = db.query(Organization).first()
    db.add(
        User(
            org_id=org.id,
            email="alice@acme.com",
            role="member",
            active=False,
            invite_token="tok-123",
        )
    )
    db.commit()

    u, err = oauth_login_outcome(db, "alice@acme.com", "google-sub-9", "Alice A")
    assert err == "" and u is not None
    assert u.active is True  # OAuth-verified email completes the invite
    assert u.invite_token is None
    assert u.oauth_provider == "google" and u.oauth_subject == "google-sub-9"
    assert u.display_name == "Alice A"


def test_deactivated_oauth_user_cannot_sign_back_in(db):
    org = db.query(Organization).first()
    db.add(
        User(
            org_id=org.id,
            email="former@acme.com",
            role="member",
            active=False,
            oauth_provider="google",
            oauth_subject="google-sub-old",
        )
    )
    db.commit()

    u, err = oauth_login_outcome(db, "former@acme.com", "google-sub-old", "Former")

    assert u is None and err == "not_invited"
    assert db.query(User).filter(User.email == "former@acme.com").one().active is False


def test_existing_active_user_logs_in_without_changing_role(db):
    org = db.query(Organization).first()
    db.add(
        User(org_id=org.id, email="admin@acme.com", role="admin", active=True, display_name="Admin")
    )
    db.commit()

    u, err = oauth_login_outcome(db, "admin@acme.com", "sub-x", "Ignored Name")
    assert err == "" and u.role == "admin"  # role unchanged
    assert u.display_name == "Admin"  # existing name preserved
    assert u.oauth_subject == "sub-x"  # provider linked on first OAuth login


def test_email_match_is_case_normalised_by_caller(db):
    # The route lowercases the email before calling; an exact-stored match works.
    org = db.query(Organization).first()
    db.add(User(org_id=org.id, email="bob@acme.com", role="member", active=True))
    db.commit()
    u, err = oauth_login_outcome(db, "bob@acme.com", "sub-b", "Bob")
    assert err == "" and u is not None


# ── registration: first run is open, an existing org stays invite-only ───────────


def test_first_run_registers_the_first_user_as_solo_admin(tmp_path):
    # Fresh install (no org at all): an OAuth identity registers ITSELF as the solo admin -
    # the OAuth equivalent of the solo-first /setup signup.
    from anthill.web.db import OrgSettings

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()

    u, err = oauth_login_outcome(
        s, "founder@gmail.com", "g-sub-1", "Ada Founder", provider="google"
    )
    s.commit()
    assert err == "" and u is not None
    assert u.role == "admin" and u.active is True
    assert u.oauth_provider == "google" and u.oauth_subject == "g-sub-1"
    assert u.hashed_password is None  # OAuth-only: the provider is the credential
    org = s.query(Organization).first()
    assert org is not None  # a solo org was created for them
    cfg = s.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    assert cfg.deployment_topology == "solo"

    # Now that an org exists, the NEXT unknown email is no longer auto-registered.
    u2, err2 = oauth_login_outcome(s, "stranger@gmail.com", "g-sub-2", "Stranger")
    assert u2 is None and err2 == "not_invited"


def test_setup_page_shows_signup_with_google_when_configured(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client-id")
    engine = create_engine(
        f"sqlite:///{tmp_path / 's.db'}", connect_args={"check_same_thread": False}
    )
    create_tables(engine)
    app_mod._engine = engine
    app_mod._SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    body = TestClient(app_mod.app).get("/setup").text  # empty DB -> first-run setup page
    assert "Sign up with Google" in body and "accounts.google.com" in body
