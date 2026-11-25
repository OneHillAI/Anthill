"""Role and account checks on the setup, org-wide settings and password-reset decisions.

- POST /setup/account-type: admin only, and only until the account type has been chosen.
- /forgot: the in-page reset link depends on the matched account's own organisation, not on whichever
  organisation row happens to come first.
- POST /personalize, /personalize/council and /setup/model: the org-wide fields (shared model, compute,
  council, PII scrub) are written by an admin only, never by a plain member. A Solo owner is the admin of
  their own account, and an account can have more than one admin.
- Password reset: a person resets only their own password; only an admin can reset anyone else's, and only
  inside their own organisation.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web.crypto import hash_password, make_token
from anthill.web.db import Organization, OrgSettings, User

PW = "correct horse battery"


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
    monkeypatch.delenv("ANTHILL_SMTP_FROM", raising=False)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return app_mod._SessionFactory()


def _org(s, name, topology, **cfg):
    o = Organization(name=name, slug=name.lower())
    s.add(o)
    s.flush()
    s.add(OrgSettings(org_id=o.id, deployment_topology=topology, **cfg))
    return o


def _user(s, org, email, role):
    u = User(
        org_id=org.id,
        email=email,
        role=role,
        active=True,
        hashed_password=hash_password(PW),
    )
    s.add(u)
    s.flush()
    return u


def _client(user):
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, user.org_id, user.role))
    return c


def _fresh(tmp_path, monkeypatch, role):
    s = _boot(tmp_path, monkeypatch)
    o = _org(s, "Acme", "org", account_type_chosen=False)
    admin = _user(s, o, "admin@acme.com", "admin")
    member = _user(s, o, "member@acme.com", "member")
    s.commit()
    return _client(admin if role == "admin" else member)


def _state():
    s = app_mod._SessionFactory()
    return s.query(OrgSettings).first(), s.query(Organization).first()


def test_member_cannot_set_the_account_type(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch, "member")
    r = c.post(
        "/setup/account-type",
        data={"account_type": "solo", "org_name": "Hijacked"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    cfg, org = _state()
    assert cfg.deployment_topology == "org" and cfg.account_type_chosen is False
    assert org.name == "Acme"


def test_admin_cannot_redo_the_account_type_once_chosen(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch, "admin")
    s = app_mod._SessionFactory()
    s.query(OrgSettings).first().account_type_chosen = True
    s.commit()
    r = c.post(
        "/setup/account-type",
        data={"account_type": "solo", "org_name": "Renamed"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/"
    cfg, org = _state()
    assert cfg.deployment_topology == "org"
    assert org.name == "Acme"


def test_first_run_admin_can_still_choose_the_account_type(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch, "admin")
    r = c.post(
        "/setup/account-type",
        data={"account_type": "org", "org_name": "Beta Corp"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/"
    cfg, org = _state()
    assert cfg.account_type_chosen is True and cfg.deployment_topology == "org"
    assert org.name == "Beta Corp"


def test_admin_can_still_convert_solo_to_org_from_settings(tmp_path, monkeypatch):
    s = _boot(tmp_path, monkeypatch)
    o = _org(s, "Personal", "solo")
    admin = _user(s, o, "owner@a.com", "admin")
    s.commit()
    r = _client(admin).post(
        "/personalize/organization/setup", data={"org_name": "Gamma"}, follow_redirects=False
    )
    assert r.status_code == 302
    cfg, org = _state()
    assert cfg.deployment_topology == "org" and org.name == "Gamma"


def _mixed(tmp_path, monkeypatch):
    """A solo organisation inserted first, then an organisation with two members."""
    s = _boot(tmp_path, monkeypatch)
    solo = _org(s, "Personal", "solo")
    _user(s, solo, "owner@home.com", "admin")
    org = _org(s, "Acme", "org")
    _user(s, org, "alice@acme.com", "member")
    _user(s, org, "boss@acme.com", "admin")
    s.commit()
    return TestClient(app_mod.app)


def test_forgot_shows_no_link_for_an_org_user_on_a_mixed_install(tmp_path, monkeypatch):
    c = _mixed(tmp_path, monkeypatch)
    matched = c.post("/forgot", data={"email": "alice@acme.com"})
    unknown = c.post("/forgot", data={"email": "ghost@nowhere.com"})
    for r in (matched, unknown):
        assert r.status_code == 200
        assert "/reset/" not in r.text and "Choose a new password" not in r.text
        assert "Ask your administrator" in r.text
    assert matched.text == unknown.text


def test_forgot_shows_no_link_for_a_solo_owner_on_a_shared_install(tmp_path, monkeypatch):
    # Strict rule: the link is shown only when the install has a single account in total, so a Solo
    # account on a shared install is treated like everyone else and the page is the same for every address.
    c = _mixed(tmp_path, monkeypatch)
    solo = c.post("/forgot", data={"email": "owner@home.com"})
    unknown = c.post("/forgot", data={"email": "ghost@nowhere.com"})
    assert "/reset/" not in solo.text and "Choose a new password" not in solo.text
    assert "Ask your administrator" in solo.text
    assert solo.text == unknown.text


def test_forgot_on_a_single_user_install_still_shows_the_link(tmp_path, monkeypatch):
    s = _boot(tmp_path, monkeypatch)
    o = _org(s, "Acme", "org")
    _user(s, o, "only@acme.com", "admin")
    s.commit()
    local = TestClient(app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = local.post("/forgot", data={"email": "only@acme.com"})
    assert "Choose a new password" in r.text and "/reset/" in r.text


def _settings_install(tmp_path, monkeypatch, topology, role, **cfg):
    s = _boot(tmp_path, monkeypatch)
    o = _org(s, "Acme", topology, **cfg)
    _user(s, o, "admin@acme.com", "admin")
    u = _user(s, o, f"{role}@acme.com", role) if role != "admin" else None
    s.commit()
    who = u or s.query(User).filter(User.email == "admin@acme.com").one()
    monkeypatch.setattr(app_mod, "_model_too_large_for_this_machine", lambda cfg, model: False)
    return _client(who)


def _org_wide():
    cfg, _ = _state()
    return cfg.ollama_model, cfg.solo_compute, bool(cfg.cloud_scrub_pii), cfg.org_council_members


def test_member_cannot_overwrite_the_shared_model_compute_or_scrub(tmp_path, monkeypatch):
    c = _settings_install(tmp_path, monkeypatch, "org", "member", cloud_scrub_pii=False)
    before = _org_wide()
    r = c.post(
        "/personalize",
        data={
            "ollama_model": "evil:70b",
            "solo_compute": "cloud",
            "scrub_pii": "on",
            "profile": "I am a member",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _org_wide() == before
    s = app_mod._SessionFactory()
    assert _core_profile(s, "member@acme.com") == "I am a member"


def _core_profile(session, email):
    """The profile a person saved: it lives on their core page (docs/specs/core-page.md), not in User.profile."""
    from anthill.wiki import core_page
    from anthill.wiki.workspace import workspace_for

    user = session.query(User).filter(User.email == email).one()
    return core_page.read(workspace_for("personal", user_id=user.id))[0]


def test_member_profile_save_works_and_leaves_org_settings_alone(tmp_path, monkeypatch):
    c = _settings_install(tmp_path, monkeypatch, "org", "member", cloud_scrub_pii=True)
    before = _org_wide()
    r = c.post("/personalize", data={"profile": "Just my profile"}, follow_redirects=False)
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    assert _org_wide() == before  # absent scrub_pii did not switch the shared setting off
    s = app_mod._SessionFactory()
    assert _core_profile(s, "member@acme.com") == "Just my profile"


def test_member_cannot_run_the_model_picker_or_council_setup(tmp_path, monkeypatch):
    c = _settings_install(tmp_path, monkeypatch, "org", "member")
    before = _org_wide()
    assert c.post("/setup/model", data={"choice": "evil:70b"}).status_code == 403
    assert c.post("/personalize/council", data={"action": "single"}).status_code == 403
    assert _org_wide() == before


def test_org_admin_can_write_the_shared_settings(tmp_path, monkeypatch):
    c = _settings_install(tmp_path, monkeypatch, "org", "admin", cloud_scrub_pii=True)
    r = c.post(
        "/personalize",
        data={"ollama_model": "qwen2.5:7b", "solo_compute": "cloud"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    model, compute, scrub, _ = _org_wide()
    assert (model, compute, scrub) == ("qwen2.5:7b", "cloud", False)
    r = c.post("/personalize/council", data={"action": "single"})
    assert r.status_code == 200 and json.loads(r.text)["mode"] == "single"


def test_solo_owner_can_write_the_shared_settings(tmp_path, monkeypatch):
    c = _settings_install(tmp_path, monkeypatch, "solo", "admin")
    r = c.post(
        "/personalize",
        data={"ollama_model": "qwen2.5:7b", "solo_compute": "cloud", "scrub_pii": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    model, compute, scrub, _ = _org_wide()
    assert (model, compute, scrub) == ("qwen2.5:7b", "cloud", True)
    assert c.post("/personalize/council", data={"action": "single"}).status_code == 200


def test_training_tick_treats_the_legacy_local_topology_as_solo(tmp_path, monkeypatch):
    import anthill.training.schedule as schedule
    from anthill.web import scheduler

    s = _boot(tmp_path, monkeypatch)
    legacy = _org(s, "Legacy", "local", training_enabled=False)
    _org(s, "Team", "org", training_enabled=False)
    s.commit()
    seen = []
    monkeypatch.setattr(
        schedule, "should_train", lambda cfg, *a, **k: seen.append(cfg.org_id) or (False, "")
    )
    scheduler._train_tick(app_mod._engine)
    assert seen == [legacy.id]


# ── the rules as the maintainer stated them ───────────────────────────────────────────


def _two_orgs(tmp_path, monkeypatch):
    """Acme with two admins and a member, and a separate organisation Other with its own member."""
    s = _boot(tmp_path, monkeypatch)
    acme = _org(s, "Acme", "org")
    admin1 = _user(s, acme, "admin1@acme.com", "admin")
    admin2 = _user(s, acme, "admin2@acme.com", "admin")
    member = _user(s, acme, "member@acme.com", "member")
    other = _org(s, "Other", "org")
    stranger = _user(s, other, "stranger@other.com", "member")
    s.commit()
    return s, admin1, admin2, member, stranger


def test_a_second_admin_can_change_shared_settings(tmp_path, monkeypatch):
    _s, _a1, admin2, _m, _x = _two_orgs(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod, "_model_too_large_for_this_machine", lambda cfg, model: False)
    r = _client(admin2).post(
        "/personalize", data={"ollama_model": "qwen2.5:7b"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert app_mod._SessionFactory().query(OrgSettings).first().ollama_model == "qwen2.5:7b"


def test_an_admin_can_reset_a_members_password_but_a_member_cannot_reset_anyone(
    tmp_path, monkeypatch
):
    _s, admin1, _a2, member, _x = _two_orgs(tmp_path, monkeypatch)
    ok = _client(admin1).post(f"/users/{member.id}/reset", follow_redirects=False)
    assert ok.status_code == 302 and "reset=member@acme.com" in ok.headers["location"]
    denied = _client(member).post(f"/users/{admin1.id}/reset", follow_redirects=False)
    assert denied.status_code == 403


def test_an_admin_cannot_reset_a_user_of_another_organisation(tmp_path, monkeypatch):
    _s, admin1, _a2, _m, stranger = _two_orgs(tmp_path, monkeypatch)
    r = _client(admin1).post(f"/users/{stranger.id}/reset", follow_redirects=False)
    assert r.status_code == 404
