"""Sign-up is by invitation on a server other people can reach (docs/specs/invite-only-signup.md).

On a server that only the person at the machine can reach, sign-up works from that machine exactly as before
(#481). On a server that listens beyond loopback, or has Remote access on, or does not say where it listens,
only the first account is open; later accounts need an invitation unless the install owner opens sign-up.
The install owner is an explicit record. Model-free.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web import install_scope
from anthill.web.crypto import make_token
from anthill.web.db import InstallSettings, Organization, OrgSettings, User

LOCAL = {"base_url": "http://127.0.0.1:8000", "client": ("127.0.0.1", 50000)}
NETWORK = {"base_url": "http://anthill.example.com", "client": ("203.0.113.5", 50000)}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org-wiki"))
    monkeypatch.setenv("ANTHILL_HOST", "127.0.0.1")  # a local server unless a test says otherwise
    monkeypatch.delenv("ANTHILL_SMTP_HOST", raising=False)
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return app_mod


def _client(kind=LOCAL, **headers):
    c = TestClient(app_mod.app, **kind)
    if headers:
        c.headers.update(headers)
    return c


def _signup(email, kind=LOCAL, **headers):
    return _client(kind, **headers).post(
        "/setup",
        data={"admin_email": email, "admin_password": "longenoughpw1"},
        follow_redirects=False,
    )


def _count(model):
    s = app_mod._SessionFactory()
    try:
        return s.query(model).count()
    finally:
        s.close()


def _signed_in(user_id, org_id, role, kind=LOCAL):
    c = _client(kind)
    c.cookies.set("session_token", make_token(user_id, org_id, role))
    return c


def _owner_client(kind=LOCAL):
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    c = _signed_in(owner.id, owner.org_id, "admin", kind)
    s.close()
    return c


def _second_org_admin():
    s = app_mod._SessionFactory()
    org = Organization(name="B", slug="b")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="admin@b.com", role="admin", active=True)
    s.add_all([admin, OrgSettings(org_id=org.id)])
    s.commit()
    out = (admin.id, org.id)
    s.close()
    return out


def _set_remote(on):
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.remote_access_provider = "manual" if on else "off"
    s.commit()
    s.close()


# ── is the server shared? ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        (None, False),  # nobody said where it listens: treated as reachable
        ("", False),
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]:8000", True),
        ("0.0.0.0", False),
        ("192.168.1.20", False),
        ("anthill.example.com", False),
    ],
)
def test_only_a_known_loopback_bind_counts_as_local(monkeypatch, host, loopback):
    if host is None:
        monkeypatch.delenv("ANTHILL_HOST", raising=False)
    else:
        monkeypatch.setenv("ANTHILL_HOST", host)
    assert install_scope.bind_is_loopback() is loopback


def test_remote_access_makes_a_loopback_server_shared(app):
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    cfg = OrgSettings(org_id=org.id, remote_access_provider="off")
    s.add(cfg)
    s.commit()
    assert install_scope.server_is_shared(s) is False
    for provider in ("cloudflare", "manual"):
        cfg.remote_access_provider = provider
        s.commit()
        assert install_scope.server_is_shared(s) is True
    s.close()


# ── sign-up through the real routes ─────────────────────────────────────────────


def test_on_a_local_server_every_account_can_sign_up_from_the_machine(app):
    assert _signup("a@example.com").status_code in (302, 303)
    assert _signup("b@example.com").status_code in (302, 303)
    assert _count(User) == 2


def test_on_a_local_server_a_request_through_a_forwarder_cannot_create_a_second_account(app):
    """Once an account exists, sign-up needs a request from the machine itself: a tunnel or proxy to the
    local port carries a public Host name or forwarding headers."""
    _signup("owner@example.com")
    assert _signup("t1@example.com", NETWORK).status_code == 403
    assert _signup("t2@example.com", **{"x-forwarded-for": "203.0.113.5"}).status_code == 403
    assert _signup("t3@example.com", **{"forwarded": "for=203.0.113.5"}).status_code == 403
    assert _count(User) == 1
    page = _client(NETWORK).get("/setup")
    assert "Sign-up is by invitation" in page.text and 'name="admin_email"' not in page.text


def test_the_first_account_is_open_even_through_the_network(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    assert _signup("owner@example.com", NETWORK).status_code in (302, 303)
    assert _count(User) == 1


def test_on_a_shared_server_later_accounts_need_an_invitation(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    assert "by invitation" not in _client().get("/setup").text
    assert _signup("owner@example.com").status_code in (302, 303)
    page = _client().get("/setup")
    assert page.status_code == 200 and "Sign-up is by invitation" in page.text
    assert 'name="admin_email"' not in page.text
    refused = _signup("later@example.com")
    assert refused.status_code == 403 and "Sign-up is by invitation" in refused.text
    assert _count(User) == 1 and _count(Organization) == 1  # nothing was created


def test_a_server_that_does_not_say_where_it_listens_is_treated_as_shared(app, monkeypatch):
    monkeypatch.delenv("ANTHILL_HOST", raising=False)
    _signup("owner@example.com")
    assert _signup("later@example.com").status_code == 403


def test_remote_access_alone_closes_sign_up_after_the_first_account(app):
    _signup("owner@example.com")
    _set_remote(True)
    assert _signup("later@example.com").status_code == 403
    assert _count(User) == 1


def test_with_users_but_no_recorded_owner_a_shared_server_stays_closed(app, monkeypatch):
    """Fail closed: accounts exist from before the owner record and nobody has been recorded yet."""
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    s = app_mod._SessionFactory()
    org = Organization(name="Old", slug="old")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="old@example.com", role="admin", active=True))
    s.commit()
    s.close()
    assert _signup("later@example.com").status_code == 403
    s = app_mod._SessionFactory()
    s.add(
        InstallSettings(signup_open=True)
    )  # even a switch that is on needs a real owner behind it
    s.commit()
    s.close()
    assert _signup("later@example.com").status_code == 403


# ── the install owner ───────────────────────────────────────────────────────────


def test_the_first_account_becomes_the_install_owner_and_a_later_one_does_not(app):
    _signup("first@example.com")
    _signup("second@example.com")
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).email == "first@example.com"
    s.close()


def test_an_install_from_before_the_record_gets_its_first_active_admin_as_owner(app):
    s = app_mod._SessionFactory()
    org = Organization(name="Old", slug="old")
    s.add(org)
    s.flush()
    s.add_all(
        [
            User(org_id=org.id, email="pending@x.com", role="admin", active=False),
            User(org_id=org.id, email="member@x.com", role="member", active=True),
            User(org_id=org.id, email="admin@x.com", role="admin", active=True),
        ]
    )
    s.commit()
    assert install_scope.install_owner(s) is None
    assert install_scope.backfill_install_owner(s) is not None
    s.commit()
    assert install_scope.install_owner(s).email == "admin@x.com"
    assert install_scope.backfill_install_owner(s) is None  # once
    s.close()


def test_an_owner_who_is_no_longer_an_active_admin_is_no_owner(app):
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    owner.active = False
    s.commit()
    assert install_scope.install_owner(s) is None
    owner.active, owner.role = True, "member"
    s.commit()
    assert install_scope.install_owner(s) is None
    s.close()


def test_an_unverified_first_admin_is_recorded_and_does_not_lock_the_operator_out(app, monkeypatch):
    """An organisation sign-up with an email server waits for the first admin to verify the address. That
    account is the owner as soon as it is active; until then the install-wide controls are shut, sign-up on a
    shared server stays closed, and the verification link (not /setup) is how the operator gets in."""
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr("anthill.web.mailer.send_verify_email", lambda *a, **k: True, raising=False)
    r = _client().post(
        "/setup",
        data={
            "admin_email": "op@example.com",
            "admin_password": "longenoughpw1",
            "topology": "org",
        },
        follow_redirects=False,
    )
    assert r.status_code in (200, 302, 303)
    s = app_mod._SessionFactory()
    first = s.query(User).filter(User.email == "op@example.com").one()
    assert first.active is False  # waiting for the verification link
    assert install_scope.install_owner(s) is None
    assert s.query(InstallSettings).first().owner_user_id == first.id  # but it is recorded
    assert _signup("other@example.com").status_code == 403
    first.active = True  # the verification link activates it
    s.commit()
    assert install_scope.install_owner(s).id == first.id
    s.close()


def test_the_owner_cannot_be_demoted_or_deactivated_by_anyone(app):
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    org_id = owner.org_id
    peer = User(org_id=org_id, email="peer@example.com", role="admin", active=True)
    s.add(peer)
    s.commit()
    peer_id, owner_id = peer.id, owner.id
    s.close()
    for client in (_owner_client(), _signed_in(peer_id, org_id, "admin")):
        r = client.post(f"/users/{owner_id}/role", data={"role": "member"}, follow_redirects=False)
        assert r.status_code == 302 and "owner_protected" in r.headers["location"]
        r = client.post(f"/users/{owner_id}/deactivate", follow_redirects=False)
        assert r.status_code == 302 and "owner_protected" in r.headers["location"]
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == owner_id
    s.close()


def test_only_the_owner_can_hand_the_install_to_an_active_admin(app):
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    org_id = owner.org_id
    heir = User(org_id=org_id, email="heir@example.com", role="admin", active=True)
    sleeper = User(org_id=org_id, email="sleeper@example.com", role="admin", active=False)
    member = User(org_id=org_id, email="member@example.com", role="member", active=True)
    s.add_all([heir, sleeper, member])
    s.commit()
    heir_id, sleeper_id, member_id = heir.id, sleeper.id, member.id
    s.close()

    heir_client = _signed_in(heir_id, org_id, "admin")
    assert (
        heir_client.post("/settings/install-owner", data={"new_owner": heir_id}).status_code == 403
    )
    owner_client = _owner_client()
    for bad in (sleeper_id, member_id):  # not active, not an admin
        r = owner_client.post(
            "/settings/install-owner", data={"new_owner": bad}, follow_redirects=False
        )
        assert "error=owner" in r.headers["location"]
    r = owner_client.post(
        "/settings/install-owner", data={"new_owner": heir_id}, follow_redirects=False
    )
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == heir_id
    s.close()
    # The previous owner has lost the controls; the new one has them.
    assert owner_client.post("/settings/signup", data={"signup_open": "1"}).status_code == 403
    assert heir_client.post("/settings/signup", data={"signup_open": "1"}).status_code in (200, 302)


# ── the owner's switch ──────────────────────────────────────────────────────────


def test_the_install_owner_can_open_sign_up_on_a_shared_server_and_close_it(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    _signup("owner@example.com")
    assert _signup("later@example.com").status_code == 403
    owner = _owner_client()
    r = owner.post("/settings/signup", data={"signup_open": "1"}, follow_redirects=False)
    assert r.status_code == 302
    assert _signup("later@example.com").status_code in (302, 303)
    assert _count(User) == 2
    owner.post("/settings/signup", data={}, follow_redirects=False)
    assert _signup("third@example.com").status_code == 403
    assert _count(User) == 2


def test_nobody_but_the_owner_can_change_the_switch(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    member = User(
        org_id=s.query(Organization).first().id, email="m@a.com", role="member", active=True
    )
    s.add(member)
    s.commit()
    member_id, member_org = member.id, member.org_id
    s.close()
    for client in (
        _signed_in(other_id, other_org, "admin"),
        _signed_in(member_id, member_org, "member"),
    ):
        assert client.post("/settings/signup", data={"signup_open": "1"}).status_code == 403
    assert _signup("later@example.com").status_code == 403


def test_the_cards_on_the_remote_access_page(app, monkeypatch):
    _signup("owner@example.com")
    local_page = _owner_client().get("/settings/remote").text
    assert "only reachable from this machine" in local_page
    assert 'name="signup_open"' not in local_page
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    page = _owner_client().get("/settings/remote").text
    assert "Who can create an account" in page and 'name="signup_open"' in page
    assert "Invite people from" in page and "backend is set up" in page  # points to the switch


# ── Remote access belongs to the install owner ──────────────────────────────────


def test_only_the_owner_can_switch_remote_access_on(app):
    """Remote access opens the whole install to the network, so it is the owner's decision whether the
    request looks local or not. A non-owner admin of another organisation cannot switch it on (which would make
    the whole server shared)."""
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    other = _signed_in(other_id, other_org, "admin", LOCAL)
    payload = {"remote_access_provider": "manual", "remote_access_url": "https://x.example.com"}
    assert other.post("/settings/remote", data=payload, follow_redirects=False).status_code == 403
    assert other.post("/settings/remote/stop").status_code == 403
    s = app_mod._SessionFactory()
    assert install_scope.remote_access_is_on(s) is False
    s.close()
    r = _owner_client(LOCAL).post("/settings/remote", data=payload, follow_redirects=False)
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    assert install_scope.remote_access_is_on(s) is True
    s.close()


def test_an_organisations_admin_can_always_switch_their_own_organisation_off_but_not_on(app):
    """The lock-in sequence: the owner has it on for two organisations; the other organisation's admin can
    undo their own and cannot set it again; the owner's off clears whatever is left."""
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual"
    s.commit()
    s.close()
    other = _signed_in(other_id, other_org, "admin", LOCAL)
    off = other.post(
        "/settings/remote", data={"remote_access_provider": "off"}, follow_redirects=False
    )
    assert off.status_code == 302
    s = app_mod._SessionFactory()
    rows = {c.org_id: c.remote_access_provider for c in s.query(OrgSettings).all()}
    s.close()
    assert rows[other_org] == "off" and [v for k, v in rows.items() if k != other_org] == ["manual"]
    again = other.post(
        "/settings/remote", data={"remote_access_provider": "manual"}, follow_redirects=False
    )
    assert again.status_code == 403
    _owner_client(LOCAL).post("/settings/remote", data={"remote_access_provider": "off"})
    s = app_mod._SessionFactory()
    assert install_scope.remote_access_is_on(s) is False
    s.close()


def test_the_remote_access_page_shows_its_form_only_to_the_owner(app):
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    owner_page = _owner_client(LOCAL).get("/settings/remote").text
    other_page = _signed_in(other_id, other_org, "admin", LOCAL).get("/settings/remote").text
    assert '<select name="remote_access_provider"' in owner_page
    assert '<select name="remote_access_provider"' not in other_page
    assert "the install owner controls it" in other_page


def test_the_owner_switching_remote_access_off_turns_it_off_for_every_organisation(app):
    """A setting another organisation saved earlier must not keep the server shared once the owner says off."""
    _signup("owner@example.com")
    _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual"
    s.commit()
    assert install_scope.remote_access_is_on(s) is True
    s.close()
    r = _owner_client(LOCAL).post(
        "/settings/remote", data={"remote_access_provider": "off"}, follow_redirects=False
    )
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    assert install_scope.remote_access_is_on(s) is False
    assert s.query(OrgSettings).filter(OrgSettings.remote_access_provider != "off").count() == 0
    s.close()


# ── invitations ─────────────────────────────────────────────────────────────────


def test_an_invited_person_can_join_a_closed_server(app, monkeypatch):
    """Invite through the real route (it needs the organisation's backend to be set up), then accept."""
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    org_id = s.query(Organization).first().id
    cfg = s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    cfg.org_backend_status = "validated"
    cfg.org_model_endpoint = "https://e/v1"
    s.commit()
    s.close()
    r = _owner_client().post(
        "/users/invite",
        data={"email": "guest@example.com", "role": "member"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "invited=" in r.headers["location"]
    s = app_mod._SessionFactory()
    token = s.query(User).filter(User.email == "guest@example.com").one().invite_token
    s.close()
    guest = _client()
    assert guest.get(f"/invite/{token}").status_code == 200
    r = guest.post(f"/invite/{token}", data={"password": "longenoughpw1"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    s = app_mod._SessionFactory()
    assert s.query(User).filter(User.email == "guest@example.com").one().active is True
    s.close()
    assert _signup("stranger@example.com").status_code == 403  # the door stayed closed


def test_without_a_set_up_backend_the_owner_is_pointed_to_the_switch(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    _signup("owner@example.com")
    r = _owner_client().post(
        "/users/invite", data={"email": "guest@example.com"}, follow_redirects=False
    )
    assert "not_activated" in r.headers["location"]  # why the invitation did not go out
    assert 'name="signup_open"' in _owner_client().get("/settings/remote").text


def test_oauth_still_creates_only_the_first_account(app, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    user, error = app_mod.oauth_login_outcome(s, "stranger@example.com", "sub-1", "Stranger")
    assert user is None and error == "not_invited"
    s.close()


# ── the entry points record where the server listens, at the moment it starts ───


def _capture_run(monkeypatch):
    import os

    import uvicorn

    seen = {}

    def fake_run(*args, **kwargs):
        seen["host_env"] = os.environ.get("ANTHILL_HOST")
        seen["host_arg"] = kwargs.get("host")
        seen["local_only"] = os.environ.get("ANTHILL_LOCAL_ONLY")

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return seen


def test_the_container_entry_point_records_the_bind_address(monkeypatch, tmp_path):
    import anthill.server as server

    monkeypatch.delenv("ANTHILL_HOST", raising=False)
    monkeypatch.setattr(server, "configure_container_env", lambda: tmp_path)
    seen = _capture_run(monkeypatch)
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    server.main()
    assert seen["host_env"] == seen["host_arg"] == "0.0.0.0"
    monkeypatch.setenv("ANTHILL_HOST", "10.0.0.5")
    server.main()
    assert seen["host_env"] == seen["host_arg"] == "10.0.0.5"
    assert seen["local_only"] is None  # the container is never marked as someone's own machine


def test_the_web_command_records_the_bind_address(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from anthill import cli

    monkeypatch.delenv("ANTHILL_HOST", raising=False)
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    monkeypatch.setenv("ANTHILL_DB", str(tmp_path / "x.db"))
    seen = _capture_run(monkeypatch)
    runner = CliRunner()
    for host in ("0.0.0.0", "127.0.0.1"):
        res = runner.invoke(cli.app, ["web", "--host", host, "--port", "8123"])
        assert res.exit_code == 0, res.output
        assert seen["host_env"] == seen["host_arg"] == host
        assert seen["local_only"] is None  # `anthill web` is shared unless the operator opts in


def test_the_desktop_entry_point_records_loopback(monkeypatch, tmp_path):
    import sys

    from anthill import desktop

    monkeypatch.delenv("ANTHILL_HOST", raising=False)
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    monkeypatch.setenv("ANTHILL_NO_BROWSER", "1")
    monkeypatch.setattr(sys, "argv", ["anthill"])
    monkeypatch.setattr(desktop, "_resolve_port", lambda: 8124)
    monkeypatch.setattr(desktop, "configure_env", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_ensure_engine_async", lambda: None)
    monkeypatch.setattr(desktop, "_exit_when_orphaned", lambda: None)
    seen = _capture_run(monkeypatch)
    desktop.main()
    assert seen["host_env"] == seen["host_arg"] == "127.0.0.1"
    assert seen["local_only"] == "1"  # the desktop app is someone's own machine


# ── a loopback bind is not enough to count as local ─────────────────────────────


@pytest.mark.parametrize(
    ("host", "flag", "local"),
    [
        ("127.0.0.1", "1", True),
        ("::1", "1", True),
        ("127.0.0.1", "", False),  # `anthill web` style: loopback bind, nobody said it is a desktop
        ("127.0.0.1", "0", False),
        ("0.0.0.0", "1", False),  # flagged but listening beyond the machine
        ("", "1", False),
    ],
)
def test_a_server_is_local_only_when_a_desktop_marks_it_and_it_listens_on_loopback(
    monkeypatch, host, flag, local
):
    monkeypatch.setenv("ANTHILL_HOST", host)
    monkeypatch.setenv("ANTHILL_LOCAL_ONLY", flag)
    assert install_scope.local_only() is local


def test_anthill_web_on_loopback_behind_a_forwarder_without_headers_cannot_add_accounts(
    app, monkeypatch
):
    """The case a reverse proxy, `ssh -R`, `socat` or `ngrok tcp` creates: a loopback bind, a loopback
    client, `Host: 127.0.0.1` and no forwarding header. Without the desktop's mark the server is shared, so
    a second sign-up is refused."""
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    _signup("owner@example.com")
    r = _signup("stranger@example.com")  # LOCAL client, no headers
    assert r.status_code == 403 and _count(User) == 1


@pytest.mark.parametrize(
    "header",
    [
        "via",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-forwarded-host",
        "true-client-ip",
        "x-client-ip",
    ],
)
def test_the_extra_proxy_headers_also_mark_a_request_as_not_local(app, header):
    _signup("owner@example.com")
    assert _signup("t@example.com", **{header: "1.2.3.4"}).status_code == 403


def test_a_desktop_still_works_for_the_machine_itself(app):
    """The marked desktop, a request from the machine: nothing changes."""
    assert _signup("a@example.com").status_code in (302, 303)
    assert _signup("b@example.com").status_code in (302, 303)
    assert _count(User) == 2
    owner = _owner_client()
    assert owner.get("/settings/remote").status_code == 200


# ── the owner is recorded in the first account's transaction ────────────────────


def test_the_owner_and_the_first_account_are_recorded_together_or_not_at_all(app, monkeypatch):
    monkeypatch.setattr(
        app_mod, "_advance_session_order", lambda *a, **k: 0
    )  # fails after the insert
    r = _signup("owner@example.com")
    assert r.status_code in (302, 303) and "/login" in r.headers["location"]
    assert _count(User) == 0 and _count(InstallSettings) == 0  # nothing half-recorded


def test_the_install_settings_are_one_row_with_a_fixed_key(app):
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    row = s.query(InstallSettings).one()
    assert row.id == install_scope.INSTALL_ROW_ID == 1
    install_scope.settings_row(s)  # asking again never makes a second row
    s.commit()
    assert s.query(InstallSettings).count() == 1
    s.close()


def test_recording_the_first_owner_is_audited(app):
    from anthill.web.db import AuditLog

    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    row = s.query(AuditLog).filter(AuditLog.event == "install.owner_recorded").one()
    assert "source=first-account" in row.detail
    s.close()


def test_the_first_oauth_account_is_the_install_owner(app):
    s = app_mod._SessionFactory()
    user, error = app_mod.oauth_login_outcome(s, "first@example.com", "sub-1", "First")
    s.commit()
    assert error == "" and install_scope.install_owner(s).id == user.id
    again, error = app_mod.oauth_login_outcome(s, "second@example.com", "sub-2", "Second")
    assert again is None and error == "not_invited"
    s.close()


def test_an_account_waiting_for_its_verification_link_becomes_the_owner_when_it_verifies(
    app, monkeypatch
):
    monkeypatch.setenv("ANTHILL_HOST", "0.0.0.0")
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr("anthill.web.mailer.send_verify_email", lambda *a, **k: True, raising=False)
    _client().post(
        "/setup",
        data={
            "admin_email": "op@example.com",
            "admin_password": "longenoughpw1",
            "topology": "org",
        },
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    pending = s.query(User).filter(User.email == "op@example.com").one()
    token, uid = pending.invite_token, pending.id
    assert install_scope.install_owner(s) is None and not pending.active
    s.close()
    r = _client().get(f"/verify/{token}", follow_redirects=False)
    assert r.status_code in (200, 302, 303)
    s = app_mod._SessionFactory()
    assert (
        install_scope.install_owner(s).id == uid
    )  # active now, and it was recorded from the start
    s.close()


# ── getting the owner back ──────────────────────────────────────────────────────


def _two_admins():
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    second = User(org_id=org.id, email="second@example.com", role="admin", active=True)
    s.add(second)
    s.commit()
    out = second.id
    s.close()
    return out


def test_start_up_can_be_told_who_the_owner_is_and_audits_it(app, monkeypatch):
    from anthill.web.db import AuditLog

    _signup("owner@example.com")
    second = _two_admins()
    monkeypatch.setenv("ANTHILL_INSTALL_OWNER", "second@example.com")
    app_mod._ensure_install_owner()
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == second
    assert s.query(AuditLog).filter(AuditLog.event == "install.owner_set_by_host").count() == 1
    s.close()
    monkeypatch.setenv("ANTHILL_INSTALL_OWNER", "nobody@example.com")  # unknown: nothing changes
    app_mod._ensure_install_owner()
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == second
    s.close()


def test_the_host_command_sets_and_shows_the_owner(app, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from anthill import cli

    _signup("owner@example.com")
    second = _two_admins()
    runner = CliRunner()
    db_file = str(tmp_path / "a.db")
    bad = runner.invoke(cli.app, ["owner", "set", "nobody@example.com", "--db", db_file])
    assert bad.exit_code == 1 and "unchanged" in bad.output
    ok = runner.invoke(cli.app, ["owner", "set", "second@example.com", "--db", db_file])
    assert ok.exit_code == 0, ok.output
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == second
    s.close()
    shown = runner.invoke(cli.app, ["owner", "show", "--db", db_file])
    assert "second@example.com" in shown.output


def test_a_recorded_owner_who_never_activated_is_replaced_by_the_first_active_admin(app):
    from anthill.web.db import AuditLog

    s = app_mod._SessionFactory()
    org = Organization(name="Old", slug="old")
    s.add(org)
    s.flush()
    ghost = User(org_id=org.id, email="ghost@x.com", role="admin", active=False)
    real = User(org_id=org.id, email="real@x.com", role="admin", active=True)
    s.add_all([ghost, real])
    s.flush()
    s.add(InstallSettings(id=1, owner_user_id=ghost.id))
    s.commit()
    real_id = real.id
    s.close()
    app_mod._ensure_install_owner()
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == real_id
    assert s.query(AuditLog).filter(AuditLog.event == "install.owner_recorded").count() == 1
    s.close()


def test_start_up_runs_the_owner_check(app, monkeypatch):
    """The real start-up hook (not just the helper) records the owner of an older install."""
    s = app_mod._SessionFactory()
    org = Organization(name="Old", slug="old")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="old@x.com", role="admin", active=True))
    s.commit()
    s.close()
    monkeypatch.setattr("anthill.web.scheduler.start_scheduler", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "_scheduler_started", False)
    for name in (
        "_reap_aws_orphans",
        "_autostart_remote_access",
        "_autostart_llm_tunnels",
        "_autostart_local_serving",
        "_warm_page_index_in_background",
        "_maybe_pull_embedding_model",
    ):
        monkeypatch.setattr(app_mod, name, lambda *a, **k: None, raising=False)
    app_mod._startup()
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).email == "old@x.com"
    s.close()


# ── protecting the owner ────────────────────────────────────────────────────────


def test_only_the_owner_can_issue_a_password_reset_for_the_owner(app):
    _signup("owner@example.com")
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    org_id, owner_id = owner.org_id, owner.id
    peer = User(org_id=org_id, email="peer@example.com", role="admin", active=True)
    member = User(org_id=org_id, email="m@example.com", role="member", active=True)
    s.add_all([peer, member])
    s.commit()
    peer_id, member_id = peer.id, member.id
    s.close()
    r = _signed_in(peer_id, org_id, "admin").post(
        f"/users/{owner_id}/reset", follow_redirects=False
    )
    assert "owner_protected" in r.headers["location"]
    s = app_mod._SessionFactory()
    assert s.get(User, owner_id).reset_token is None  # no link was issued
    s.close()
    mine = _owner_client().post(f"/users/{owner_id}/reset", follow_redirects=False)
    assert "reset=" in mine.headers["location"]
    other = _signed_in(peer_id, org_id, "admin").post(
        f"/users/{member_id}/reset", follow_redirects=False
    )
    assert "reset=" in other.headers["location"]  # ordinary members are unchanged


def test_the_transfer_list_names_only_admins_of_the_owners_own_organisation(app):
    _signup("owner@example.com")
    _two_admins()  # same organisation
    _second_org_admin()  # another organisation: admin@b.com
    page = _owner_client().get("/settings/remote").text
    assert "second@example.com" in page and "admin@b.com" not in page
    s = app_mod._SessionFactory()
    foreign = s.query(User).filter(User.email == "admin@b.com").one().id
    s.close()
    r = _owner_client().post(
        "/settings/install-owner", data={"new_owner": foreign}, follow_redirects=False
    )
    assert "error=owner" in r.headers["location"]
    assert (
        "cannot be made the install owner"
        in _owner_client().get("/settings/remote?error=owner").text
    )


# ── one tunnel for the whole install ────────────────────────────────────────────


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.stderr = None

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def _tunnel_up():
    from anthill.remote import tunnel

    proc = _FakeProcess()
    tunnel.manager.stop()
    tunnel.manager.start("cloudflare", 8000, "", spawn=lambda cmd: proc)
    return tunnel, proc


def test_another_organisations_off_leaves_the_owners_tunnel_running(app):
    """One tunnel serves the whole install, so a non-owner switching their own organisation off must not stop
    it while another organisation's setting is still on."""
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "cloudflare"
    s.commit()
    s.close()
    tunnel, proc = _tunnel_up()
    try:
        r = _signed_in(other_id, other_org, "admin").post(
            "/settings/remote", data={"remote_access_provider": "off"}, follow_redirects=False
        )
        assert r.status_code == 302
        assert proc.terminated is False and tunnel.manager.status()["running"] is True
        s = app_mod._SessionFactory()
        rows = {c.org_id: c.remote_access_provider for c in s.query(OrgSettings).all()}
        s.close()
        assert rows[other_org] == "off" and "cloudflare" in rows.values()
        # the owner's own off then stops it for everyone
        _owner_client().post("/settings/remote", data={"remote_access_provider": "off"})
        assert proc.terminated is True
    finally:
        tunnel.manager.stop()


def test_the_last_organisation_switching_off_stops_the_tunnel(app):
    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).filter(OrgSettings.org_id == other_org):
        cfg.remote_access_provider = "cloudflare"  # only this organisation has it on
    s.commit()
    s.close()
    tunnel, proc = _tunnel_up()
    try:
        _signed_in(other_id, other_org, "admin").post(
            "/settings/remote", data={"remote_access_provider": "off"}
        )
        assert proc.terminated is True  # nothing else is on: the tunnel goes with it
    finally:
        tunnel.manager.stop()


def _second_admin_in_the_owners_org():
    s = app_mod._SessionFactory()
    owner = install_scope.install_owner(s)
    admin = User(org_id=owner.org_id, email="second@example.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    out = (admin.id, owner.org_id)
    s.close()
    return out


def test_a_second_admin_in_the_owners_organisation_cannot_switch_remote_access_off(app):
    """The owner's organisation row is the one that keeps the install's tunnel running, so an admin there who
    is not the install owner is refused: the row, the token, the URL and the tunnel stay as they were."""
    from anthill.web.crypto import encrypt

    _signup("owner@example.com")
    second_id, owner_org = _second_admin_in_the_owners_org()
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).filter(OrgSettings.org_id == owner_org).one()
    cfg.remote_access_provider = "cloudflare"
    cfg.remote_access_token_enc = encrypt("real-tunnel-token")
    cfg.remote_access_url = "https://real.example.com"
    s.commit()
    saved_token = cfg.remote_access_token_enc
    s.close()
    tunnel, proc = _tunnel_up()
    try:
        second = _signed_in(second_id, owner_org, "admin")
        for payload in (
            {"remote_access_provider": "off"},
            {
                "remote_access_provider": "off",
                "remote_access_token": "planted",
                "remote_access_url": "https://planted.example.com",
            },
        ):
            r = second.post("/settings/remote", data=payload, follow_redirects=False)
            assert r.status_code == 403
        assert proc.terminated is False and tunnel.manager.status()["running"] is True
        s = app_mod._SessionFactory()
        cfg = s.query(OrgSettings).filter(OrgSettings.org_id == owner_org).one()
        assert cfg.remote_access_provider == "cloudflare"
        assert cfg.remote_access_token_enc == saved_token
        assert cfg.remote_access_url == "https://real.example.com"
        assert install_scope.remote_access_is_on(s) is True
        s.close()
    finally:
        tunnel.manager.stop()


def test_a_non_owner_off_changes_only_the_provider_of_their_own_organisation(app):
    """Switching off never writes a token or a URL, whatever the form carries."""
    from anthill.web.crypto import encrypt

    _signup("owner@example.com")
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual"
        cfg.remote_access_token_enc = encrypt("token-" + str(cfg.org_id))
        cfg.remote_access_url = f"https://{cfg.org_id}.example.com"
    s.commit()
    before = {
        c.org_id: (c.remote_access_token_enc, c.remote_access_url)
        for c in s.query(OrgSettings).all()
    }
    s.close()
    r = _signed_in(other_id, other_org, "admin").post(
        "/settings/remote",
        data={
            "remote_access_provider": "off",
            "remote_access_token": "planted",
            "remote_access_url": "https://planted.example.com",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    rows = {c.org_id: c for c in s.query(OrgSettings).all()}
    assert rows[other_org].remote_access_provider == "off"
    assert [c.remote_access_provider for k, c in rows.items() if k != other_org] == ["manual"]
    after = {k: (c.remote_access_token_enc, c.remote_access_url) for k, c in rows.items()}
    s.close()
    assert after == before


def test_the_off_button_is_hidden_from_admins_of_the_owners_organisation(app):
    _signup("owner@example.com")
    second_id, owner_org = _second_admin_in_the_owners_org()
    other_id, other_org = _second_org_admin()
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual"
    s.commit()
    s.close()
    inside = _signed_in(second_id, owner_org, "admin").get("/settings/remote").text
    outside = _signed_in(other_id, other_org, "admin").get("/settings/remote").text
    assert "Switch Remote access off for my organisation" not in inside
    assert "Switch Remote access off for my organisation" in outside


# ── the host commands ───────────────────────────────────────────────────────────


def test_the_owner_commands_find_the_database_from_the_home_folder(app, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from anthill import cli

    _signup("owner@example.com")
    monkeypatch.delenv("ANTHILL_DB", raising=False)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))  # the fixture's database is tmp_path / a.db
    import sqlite3

    source, target = (
        sqlite3.connect(str(tmp_path / "a.db")),
        sqlite3.connect(str(tmp_path / "anthill.db")),
    )
    source.backup(target)  # a consistent copy, whatever is still in the write-ahead log
    source.close()
    target.close()
    shown = CliRunner().invoke(cli.app, ["owner", "show"])
    assert shown.exit_code == 0 and "owner@example.com" in shown.output


def test_the_owner_commands_refuse_to_run_against_a_missing_database(app, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from anthill import cli

    monkeypatch.delenv("ANTHILL_DB", raising=False)
    monkeypatch.delenv("ANTHILL_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    missing = str(tmp_path / "nowhere" / "anthill.db")
    runner = CliRunner()
    for args in (["owner", "show", "--db", missing], ["owner", "set", "a@b.com", "--db", missing]):
        res = runner.invoke(cli.app, args)
        assert res.exit_code == 1 and "No database" in res.output
    assert not (tmp_path / "nowhere").exists()  # and nothing was created


def test_the_show_command_takes_a_profile_like_the_others():
    from typer.testing import CliRunner

    from anthill import cli

    for command in ("show", "set"):
        helptext = CliRunner().invoke(cli.app, ["owner", command, "--help"]).output
        assert "--profile" in helptext and "--db" in helptext


def test_the_owner_commands_read_a_named_profile_without_activating_it(app, tmp_path, monkeypatch):
    import sqlite3

    from typer.testing import CliRunner

    from anthill import cli
    from anthill import profiles as profiles_mod

    _signup("owner@example.com")
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(cli, "_profiles_base", lambda: base)
    monkeypatch.delenv("ANTHILL_DB", raising=False)
    monkeypatch.delenv("ANTHILL_HOME", raising=False)
    monkeypatch.delenv("ANTHILL_PROFILE", raising=False)
    work, _ = profiles_mod.create_profile(base, "work")
    source = sqlite3.connect(str(tmp_path / "a.db"))
    target = sqlite3.connect(str(base / "profiles" / work["id"] / "anthill.db"))
    source.backup(target)
    source.close()
    target.close()
    before = profiles_mod.load_registry(base)
    shown = CliRunner().invoke(cli.app, ["owner", "show", "--profile", "work"])
    assert shown.exit_code == 0 and "owner@example.com" in shown.output
    assert profiles_mod.load_registry(base) == before  # the remembered profile did not change
    assert "ANTHILL_HOME" not in os.environ

    unknown = CliRunner().invoke(cli.app, ["owner", "show", "--profile", "no-such-profile"])
    assert unknown.exit_code == 1 and "No profile named" in unknown.output
    assert profiles_mod.load_registry(base) == before
    unknown_set = CliRunner().invoke(
        cli.app, ["owner", "set", "owner@example.com", "--profile", "no-such-profile"]
    )
    assert unknown_set.exit_code == 1


def test_an_unknown_host_named_owner_falls_back_to_the_recorded_one_and_a_change_is_logged(
    app, monkeypatch, caplog
):
    import logging

    _signup("owner@example.com")
    second = _two_admins()
    monkeypatch.setenv("ANTHILL_INSTALL_OWNER", "nobody@example.com")
    with caplog.at_level(logging.WARNING):
        app_mod._ensure_install_owner()
    assert "names no active admin" in caplog.text
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).email == "owner@example.com"  # unchanged
    s.close()
    # an inactive recorded owner still falls through to the first active admin
    s = app_mod._SessionFactory()
    s.query(User).filter(User.email == "owner@example.com").update({"active": False})
    s.commit()
    s.close()
    app_mod._ensure_install_owner()
    s = app_mod._SessionFactory()
    assert install_scope.install_owner(s).id == second
    s.close()
    # naming a different active admin overrides the recorded owner and says so
    caplog.clear()
    s = app_mod._SessionFactory()
    s.query(User).filter(User.email == "owner@example.com").update({"active": True})
    s.commit()
    s.close()
    monkeypatch.setenv("ANTHILL_INSTALL_OWNER", "owner@example.com")
    with caplog.at_level(logging.WARNING):
        app_mod._ensure_install_owner()
    assert "replaced the recorded install owner" in caplog.text


# ── the launchers carry the mark ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "honours_existing"),
    [
        ("start.sh", "${ANTHILL_LOCAL_ONLY-1}"),
        ("Makefile", "${ANTHILL_LOCAL_ONLY-1}"),
        ("scripts/install-autostart.sh", "${ANTHILL_LOCAL_ONLY-1}"),
        ("scripts/test-setup.sh", "${ANTHILL_LOCAL_ONLY-1}"),
        ("scripts/start.ps1", "$null -eq $env:ANTHILL_LOCAL_ONLY"),
    ],
)
def test_each_personal_launcher_sets_the_mark_unless_one_is_already_set(path, honours_existing):
    from pathlib import Path

    root = Path(app_mod.__file__).resolve().parents[2]
    text = (root / path).read_text()
    assert "ANTHILL_LOCAL_ONLY" in text and honours_existing in text, path


def test_the_powershell_launcher_puts_the_variables_back(app):
    from pathlib import Path

    text = (Path(app_mod.__file__).resolve().parents[2] / "scripts" / "start.ps1").read_text()
    assert "finally" in text and "$previousLocal" in text and "$previousHost" in text
