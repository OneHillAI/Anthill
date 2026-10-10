"""Backup and restore need the install owner (docs/specs/backup-install-owner.md).

A backup covers the whole install, not one organisation: every organisation's data and the install's keys. So
making one and restoring one follow the same rule as the other install-wide controls: the install owner
always; anyone else only on a local server (the desktop app, Remote access off) and from the machine itself.
Model-free.
"""

import io
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import InstallSettings, Organization, OrgSettings, User


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key, sub in (
        ("ANTHILL_HOME", ""),
        ("ANTHILL_WORKSPACE", "workspace"),
        ("ANTHILL_FILES_DIR", "files"),
        ("ANTHILL_SKILLS_DIR", "skills"),
        ("ANTHILL_WIKI_ROOT", "wikis"),
        ("ANTHILL_ORG_WIKI", "org-wiki"),
    ):
        monkeypatch.setenv(key, str(home / sub) if sub else str(home))
    monkeypatch.setenv("ANTHILL_DB", str(home / "anthill.db"))
    (home / "secrets.env").write_text("ANTHILL_ENCRYPTION_KEY=abc\n")
    (home / "workspace").mkdir()
    (home / "workspace" / "note.md").write_text("original\n")
    eng = create_engine(
        f"sqlite:///{home / 'anthill.db'}", connect_args={"check_same_thread": False}
    )
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
    other = User(org_id=two.id, email="admin@two.com", role="admin", active=True)
    s.add_all([owner, member, other, OrgSettings(org_id=one.id), OrgSettings(org_id=two.id)])
    s.flush()
    s.add(InstallSettings(id=1, owner_user_id=owner.id))
    s.commit()
    people = {
        "owner": (owner.id, one.id, "admin"),
        "member": (member.id, one.id, "member"),
        "other": (other.id, two.id, "admin"),
    }
    s.close()
    return SimpleNamespace(home=home, people=people)


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


def _archive(world, owner_client):
    r = owner_client.get("/backup/export?model=0")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    return r.content


def _remote_access(on):
    s = app_mod._SessionFactory()
    for cfg in s.query(OrgSettings).all():
        cfg.remote_access_provider = "manual" if on else "off"
    s.commit()
    s.close()


def _db_ready(world):
    """A real database file for the backup to copy."""
    con = sqlite3.connect(str(world.home / "anthill.db"))
    con.execute("CREATE TABLE IF NOT EXISTS t(x)")
    con.commit()
    con.close()


# ── export ──────────────────────────────────────────────────────────────────────


def test_the_install_owner_can_download_a_backup_over_the_network(world):
    _db_ready(world)
    data = _archive(world, _client(world, "owner"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        assert any(n.endswith("secrets.env") for n in tar.getnames())


@pytest.mark.parametrize("who", ["other", "member"])
def test_another_organisations_admin_and_a_member_cannot_download_a_backup(world, who):
    _db_ready(world)
    r = _client(world, who).get("/backup/export?model=0")
    assert r.status_code == 403
    assert "secrets" not in r.text and r.headers.get("content-type") != "application/gzip"


def test_an_admin_on_the_machine_of_a_local_server_can_still_download_a_backup(world):
    _db_ready(world)
    assert _client(world, "other", local=True).get("/backup/export?model=0").status_code == 200


def test_a_request_that_looks_local_on_a_shared_server_is_not_enough(world):
    _db_ready(world)
    _remote_access(True)
    assert _client(world, "other", local=True).get("/backup/export?model=0").status_code == 403
    assert _client(world, "owner", local=True).get("/backup/export?model=0").status_code == 200


def test_without_the_desktop_mark_a_loopback_request_is_not_enough(world, monkeypatch):
    _db_ready(world)
    monkeypatch.delenv("ANTHILL_LOCAL_ONLY", raising=False)
    assert _client(world, "other", local=True).get("/backup/export?model=0").status_code == 403


# ── restore ─────────────────────────────────────────────────────────────────────


def _restore(client, data):
    return client.post(
        "/backup/restore",
        files={"file": ("backup.tar.gz", data, "application/gzip")},
        follow_redirects=False,
    )


def test_the_install_owner_can_restore_and_another_admin_cannot(world):
    _db_ready(world)
    data = _archive(world, _client(world, "owner"))
    note = world.home / "workspace" / "note.md"
    note.write_text("changed after the backup\n")

    refused = _restore(_client(world, "other"), data)
    assert refused.status_code == 403
    assert note.read_text() == "changed after the backup\n"  # nothing was touched
    assert _restore(_client(world, "member"), data).status_code == 403

    ok = _restore(_client(world, "owner"), data)
    assert ok.status_code == 302 and "restored=1" in ok.headers["location"]
    assert note.read_text() == "original\n"


def test_a_local_admin_can_restore_on_a_desktop_and_not_once_remote_access_is_on(world):
    _db_ready(world)
    data = _archive(world, _client(world, "owner"))
    assert _restore(_client(world, "other", local=True), data).status_code == 302
    _db_ready(world)  # the restore replaced the database; make sure there is a usable file again
    _remote_access(True)
    assert _restore(_client(world, "other", local=True), data).status_code == 403


# ── the page ────────────────────────────────────────────────────────────────────


def test_the_backup_page_shows_the_controls_only_to_someone_who_can_use_them(world):
    _db_ready(world)
    owner_page = _client(world, "owner").get("/backup").text
    assert "/backup/export" in owner_page and 'action="/backup/restore"' in owner_page
    assert str(world.home) in owner_page
    other_page = _client(world, "other").get("/backup").text
    assert "/backup/export" not in other_page and "/backup/restore" not in other_page
    assert str(world.home) not in other_page
    assert "covers the whole install, not one organisation" in other_page
    local_page = _client(world, "other", local=True).get("/backup").text
    assert "/backup/export" in local_page
