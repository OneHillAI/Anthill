"""Install-wide controls belong to the install owner or the machine itself
(docs/specs/install-owner-controls.md): device profiles (create, rename, delete) and the built-in skills every
organisation on the install shares. Model-free.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill import profiles
from anthill.web.crypto import make_token
from anthill.web.db import InstallSettings, Organization, OrgSettings, User
from anthill.web.install_scope import is_install_owner, request_is_local


@pytest.fixture
def world(tmp_path, monkeypatch):
    base = tmp_path / "appdata"
    base.mkdir()
    skills = tmp_path / "skills"
    (skills / "starter").mkdir(parents=True)
    (skills / "starter" / "SKILL.md").write_text("---\nname: starter\ndescription: d\n---\nbody\n")
    monkeypatch.setenv("ANTHILL_PROFILES_BASE", str(base))
    monkeypatch.setenv("ANTHILL_HOME", str(base))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(skills))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    one = Organization(name="One", slug="one")
    two = Organization(name="Two", slug="two")
    s.add_all([one, two])
    s.flush()
    owner = User(org_id=one.id, email="owner@one.com", role="admin", active=True)
    member = User(org_id=one.id, email="member@one.com", role="member", active=True)
    other_admin = User(org_id=two.id, email="admin@two.com", role="admin", active=True)
    s.add_all([owner, member, other_admin, OrgSettings(org_id=one.id), OrgSettings(org_id=two.id)])
    s.flush()
    s.add(
        InstallSettings(owner_user_id=owner.id)
    )  # the first account is the recorded install owner
    s.commit()
    people = {
        "owner": (owner.id, one.id, "admin"),
        "member": (member.id, one.id, "member"),
        "other": (other_admin.id, two.id, "admin"),
    }
    s.close()
    profile, _over_cap = profiles.create_profile(base, "Work", None)
    return SimpleNamespace(
        base=base, skills=skills, people=people, profile_id=profile["id"], profile=profile
    )


def _client(world, who, *, local=False, headers=None):
    uid, oid, role = world.people[who]
    if local:
        c = TestClient(app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    else:
        c = TestClient(
            app_mod.app, base_url="http://anthill.example.com", client=("203.0.113.5", 50000)
        )
    c.cookies.set("session_token", make_token(uid, oid, role))
    if headers:
        c.headers.update(headers)
    return c


def _profile_names(world):
    reg = profiles.migrate_or_init(world.base)
    return {p["name"] for p in reg["profiles"]}


# ── is this request from the machine itself? ────────────────────────────────────


def _req(client_host, headers):
    return SimpleNamespace(client=SimpleNamespace(host=client_host), headers=headers)


@pytest.mark.parametrize(
    ("client_host", "headers", "local"),
    [
        ("127.0.0.1", {"host": "127.0.0.1:8000"}, True),
        ("127.0.0.1", {"host": "localhost:8000"}, True),
        ("::1", {"host": "[::1]:8000"}, True),
        ("203.0.113.5", {"host": "127.0.0.1:8000"}, False),  # a network client
        (
            "127.0.0.1",
            {"host": "anthill.example.com"},
            False,
        ),  # a tunnel: addressed to the public name
        ("127.0.0.1", {"host": "127.0.0.1", "x-forwarded-for": "203.0.113.5"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "forwarded": "for=203.0.113.5"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "cf-connecting-ip": "203.0.113.5"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "x-real-ip": "203.0.113.5"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "via": "1.1 proxy"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "x-forwarded-host": "qa.example.com"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "x-forwarded-proto": "https"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "x-forwarded-port": "443"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "true-client-ip": "203.0.113.5"}, False),
        ("127.0.0.1", {"host": "127.0.0.1", "x-client-ip": "203.0.113.5"}, False),
        ("127.0.0.1", {}, False),  # no Host header at all
    ],
)
def test_only_a_request_from_the_machine_itself_is_local(client_host, headers, local):
    assert request_is_local(_req(client_host, headers)) is local


def test_a_request_with_no_client_address_is_not_local():
    assert request_is_local(SimpleNamespace(client=None, headers={"host": "127.0.0.1"})) is False


def test_the_install_owner_is_the_first_admin(world):
    s = app_mod._SessionFactory()
    uid, oid, _ = world.people["owner"]
    assert is_install_owner(s, {"sub": str(uid), "org": oid, "role": "admin"}) is True
    uid2, oid2, _ = world.people["other"]
    assert is_install_owner(s, {"sub": str(uid2), "org": oid2, "role": "admin"}) is False
    assert is_install_owner(s, {"sub": str(uid), "org": oid, "role": "member"}) is False
    assert is_install_owner(s, {}) is False
    s.close()


# ── profiles ────────────────────────────────────────────────────────────────────


def test_the_install_owner_can_manage_profiles_over_the_network(world):
    c = _client(world, "owner")
    assert c.post("/profiles", data={"name": "Extra"}, follow_redirects=False).status_code == 303
    assert "Extra" in _profile_names(world)
    r = c.post(
        f"/profiles/{world.profile_id}/edit", data={"name": "Renamed"}, follow_redirects=False
    )
    assert r.status_code == 303 and "Renamed" in _profile_names(world)
    r = c.post(f"/profiles/{world.profile_id}/delete", follow_redirects=False)
    assert r.status_code == 303 and "Renamed" not in _profile_names(world)


@pytest.mark.parametrize("who", ["other", "member"])
def test_anyone_else_over_the_network_cannot_create_rename_or_delete_a_profile(world, who):
    c = _client(world, who)
    before = _profile_names(world)
    assert c.post("/profiles", data={"name": "Mine"}, follow_redirects=False).status_code == 403
    r = c.post(f"/profiles/{world.profile_id}/edit", data={"name": "Taken"}, follow_redirects=False)
    assert r.status_code == 403
    assert c.post(f"/profiles/{world.profile_id}/delete", follow_redirects=False).status_code == 403
    assert _profile_names(world) == before
    assert profiles.data_dir_of(world.base, world.profile).exists()


def test_a_request_from_the_machine_itself_may_manage_profiles(world):
    c = _client(world, "member", local=True)
    assert c.post("/profiles", data={"name": "Local"}, follow_redirects=False).status_code == 303
    assert "Local" in _profile_names(world)


def test_a_tunnel_to_a_local_port_is_not_local(world):
    c = _client(
        world,
        "other",
        local=True,
        headers={"x-forwarded-for": "203.0.113.5", "host": "abc.trycloudflare.com"},
    )
    assert (
        c.post("/profiles", data={"name": "Tunnelled"}, follow_redirects=False).status_code == 403
    )


def test_the_profiles_page_hides_the_controls_from_someone_who_cannot_use_them(world):
    page = _client(world, "other").get("/profiles").text
    assert "Create a profile" not in page and "/delete" not in page and "/edit" not in page
    assert "managed by the owner of this install" in page
    owner_page = _client(world, "owner").get("/profiles").text
    assert "Create a profile" in owner_page and "/edit" in owner_page


# ── built-in skills ─────────────────────────────────────────────────────────────


def _delete_builtin(client):
    return client.post("/skills/starter/delete", data={"scope": "builtin"}, follow_redirects=False)


def test_another_organisations_admin_cannot_delete_a_built_in_skill(world):
    r = _delete_builtin(_client(world, "other"))
    assert r.status_code == 403
    assert (world.skills / "starter").exists()


def test_a_member_cannot_delete_a_built_in_skill(world):
    assert _delete_builtin(_client(world, "member")).status_code == 403
    assert (world.skills / "starter").exists()


def test_the_install_owner_can_delete_a_built_in_skill(world):
    r = _delete_builtin(_client(world, "owner"))
    assert r.status_code in (302, 303)
    assert not (world.skills / "starter").exists()


def test_an_admin_on_the_machine_itself_can_delete_a_built_in_skill(world):
    r = _delete_builtin(_client(world, "other", local=True))
    assert r.status_code in (302, 303)
    assert not (world.skills / "starter").exists()


# ── a shared server accepts only the install owner ──────────────────────────────


def _remote_access(on):
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual" if on else "off"
    s.commit()
    s.close()


def test_on_a_shared_server_a_request_that_looks_local_is_not_enough(world):
    """Loopback client, Host 127.0.0.1 and none of the forwarding headers: what a plain proxy, `ssh -L`,
    `socat` or `tailscale serve` on the same machine produces. With Remote access on, only the install
    owner may manage profiles and delete built-in skills."""
    _remote_access(True)
    other = _client(world, "other", local=True)  # an admin of another organisation
    member = _client(world, "member", local=True)
    for c in (other, member):
        assert (
            c.post("/profiles", data={"name": "Sneaky"}, follow_redirects=False).status_code == 403
        )
        r = c.post(f"/profiles/{world.profile_id}/edit", data={"name": "X"}, follow_redirects=False)
        assert r.status_code == 403
        assert (
            c.post(f"/profiles/{world.profile_id}/delete", follow_redirects=False).status_code
            == 403
        )
    assert _delete_builtin(other).status_code == 403
    assert (world.skills / "starter").exists()
    assert "Sneaky" not in _profile_names(world)
    # the owner still can, from anywhere
    assert _client(world, "owner", local=True).post(
        "/profiles", data={"name": "Fine"}
    ).status_code in (
        200,
        303,
    )


def test_a_server_that_does_not_say_where_it_listens_trusts_only_the_owner(world, monkeypatch):
    monkeypatch.delenv("ANTHILL_HOST", raising=False)
    assert (
        _client(world, "other", local=True).post("/profiles", data={"name": "X"}).status_code == 403
    )
    assert _client(world, "owner").post("/profiles", data={"name": "Y"}).status_code in (200, 303)


def test_the_owner_who_is_no_longer_an_active_admin_loses_the_controls(world):
    s = app_mod._SessionFactory()
    owner = s.query(User).filter(User.email == "owner@one.com").one()
    owner.role = "member"
    s.commit()
    s.close()
    # nobody over the network (no owner), but the machine itself still works on a local server
    assert _client(world, "owner").post("/profiles", data={"name": "X"}).status_code == 403


# ── what the pages show ─────────────────────────────────────────────────────────


def test_the_skills_page_shows_delete_on_a_built_in_skill_only_to_someone_who_may_use_it(world):
    def delete_forms(client):
        page = client.get("/skills").text
        return page.count('action="/skills/starter/delete"')

    assert delete_forms(_client(world, "owner")) == 1
    assert delete_forms(_client(world, "other")) == 0  # over the network, not the owner
    assert (
        delete_forms(_client(world, "other", local=True)) == 1
    )  # on the machine of a local server
    _remote_access(True)
    assert delete_forms(_client(world, "other", local=True)) == 0  # shared: owner only
    assert delete_forms(_client(world, "owner")) == 1


def test_the_account_profile_page_shows_the_rename_form_only_to_someone_who_can_use_it(world):
    def has_form(client):
        page = client.get("/profile").text
        return f"/profiles/{world.profile_id}/edit" in page or "/profiles/default/edit" in page

    assert has_form(_client(world, "owner")) is True
    assert has_form(_client(world, "other")) is False
    assert has_form(_client(world, "other", local=True)) is True
    _remote_access(True)
    assert has_form(_client(world, "other", local=True)) is False


def test_anthill_web_style_loopback_without_the_desktop_mark_trusts_only_the_owner(
    world, monkeypatch
):
    """A loopback bind that nobody marked as a desktop: a plain proxy, `ssh -R`, `socat` or `ngrok tcp` on the
    same machine produces a loopback client, Host 127.0.0.1 and no header. It must not unlock the controls."""
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    other = _client(world, "other", local=True)
    assert other.post("/profiles", data={"name": "X"}, follow_redirects=False).status_code == 403
    assert _delete_builtin(other).status_code == 403
    assert (world.skills / "starter").exists()
    assert _client(world, "owner", local=True).post(
        "/profiles", data={"name": "Fine"}
    ).status_code in (200, 303)
