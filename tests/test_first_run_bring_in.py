"""First run: a person with an earlier copy's data can bring their account in instead of only setting up.

A copy run from a project folder keeps its database, wikis and secrets in that folder; the installed app
keeps them in its own data home. Spec: docs/specs/first-run-bring-in-existing-data.md.
"""

from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web import bring_in, crypto
from anthill.web.db import Organization, OrgSettings, User

EMAIL = "old@example.com"
PASSWORD = "an-old-long-password"
OLD_KEY = base64.b64encode(b"o" * 32).decode()
OLD_JWT = "old-jwt-secret-value"
FRESH_KEY = base64.b64encode(b"f" * 32).decode()
FRESH_JWT = "fresh-jwt-secret-value"
STORED_SECRET = "tunnel-key-the-user-stored"


def _client(*, host="127.0.0.1", client=("127.0.0.1", 50000)):
    return TestClient(app_mod.app, base_url=f"http://{host}:8000", client=client)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "App Support" / "Anthill"
    h.mkdir(parents=True)
    for var, rel in {
        "ANTHILL_HOME": "",
        "ANTHILL_DB": "anthill.db",
        "ANTHILL_WORKSPACE": "workspace",
        "ANTHILL_FILES_DIR": "files",
        "ANTHILL_SKILLS_DIR": "skills",
        "ANTHILL_WIKI_ROOT": "wikis",
        "ANTHILL_ORG_WIKI": "org-wiki",
        "ANTHILL_ORG_WIKIS": "org-wikis",
    }.items():
        monkeypatch.setenv(var, str(h / rel) if rel else str(h))
    monkeypatch.setattr(db_mod, "DB_PATH", h / "anthill.db")
    monkeypatch.setattr(crypto, "_KEY", base64.b64decode(FRESH_KEY))
    monkeypatch.setattr(crypto, "_JWT_SECRET", FRESH_JWT)
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", FRESH_KEY)
    monkeypatch.setenv("ANTHILL_JWT_SECRET", FRESH_JWT)
    (h / "secrets.env").write_text(
        f"ANTHILL_JWT_SECRET={FRESH_JWT}\nANTHILL_ENCRYPTION_KEY={FRESH_KEY}\n"
    )
    os.chmod(h / "secrets.env", 0o600)
    eng = db_mod.get_engine()
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    yield h
    if app_mod._engine is not None:
        app_mod._engine.dispose()
    app_mod._engine = None
    app_mod._SessionFactory = None


def _old_project(root: Path, *, env: str | None, dispose=True) -> Path:
    """A project folder an earlier copy was started from: data/anthill.db built through the app's models."""
    project = root / "old project"
    (project / "data" / "wikis" / "user-1").mkdir(parents=True)
    (project / "data" / "wikis" / "user-1" / "page.md").write_text("# Old page\nkept\n")
    (project / "data" / "files").mkdir()
    (project / "data" / "files" / "report.txt").write_text("report")
    (project / "data" / "skills" / "mine").mkdir(parents=True)
    (project / "data" / "skills" / "mine" / "SKILL.md").write_text("my skill")
    (project / "workspace").mkdir()
    (project / "workspace" / "note.md").write_text("workspace note")
    if env is not None:
        (project / ".env").write_text(env)
    eng = db_mod.get_engine(project / "data" / "anthill.db")
    db_mod.create_tables(eng)
    original = crypto._KEY
    crypto._KEY = base64.b64decode(OLD_KEY)
    try:
        stored = crypto.encrypt(STORED_SECRET)
    finally:
        crypto._KEY = original
    s = sessionmaker(bind=eng)()
    org = Organization(name="Personal", slug="personal")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="solo", org_lambda_tunnel_key_enc=stored))
    s.add(
        User(
            org_id=org.id,
            email=EMAIL,
            display_name="Old Me",
            hashed_password=crypto.hash_password(PASSWORD),
            role="admin",
            active=True,
        )
    )
    s.commit()
    s.close()
    if dispose:
        eng.dispose()
    return project


GOOD_ENV = (
    f"ANTHILL_JWT_SECRET={OLD_JWT}\nANTHILL_ENCRYPTION_KEY={OLD_KEY}\nAWS_SECRET=never-copied\n"
)


def _snapshot(h: Path) -> dict[str, bytes]:
    skip = {"anthill.db-wal", "anthill.db-shm"}
    return {
        str(p.relative_to(h)): p.read_bytes()
        for p in sorted(h.rglob("*"))
        if p.is_file() and p.name not in skip and "backups" not in p.parts
    }


def _bring(c, folder, **extra):
    return c.post("/setup/bring-in", data={"folder": str(folder), **extra}, follow_redirects=False)


def _login(c):
    return c.post("/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False)


# ── the first-run page ─────────────────────────────────────────────────────────


def test_first_run_page_offers_bringing_in_and_names_the_data_folder(home):
    r = _client().get("/setup")
    assert r.status_code == 200
    assert "Bring in your account" in r.text
    assert 'action="/setup/bring-in"' in r.text and 'name="folder"' in r.text
    assert f"This app keeps its data in <code>{home}</code>" in r.text


def test_login_on_an_empty_install_still_goes_to_setup(home):
    r = _client().get("/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/setup"


def test_no_bring_in_section_for_a_shared_server_or_a_remote_visitor(home, monkeypatch):
    remote = _client(host="anthill.example.com", client=("203.0.113.5", 50000))
    assert "Bring in your account" not in remote.get("/setup").text
    monkeypatch.setenv("ANTHILL_LOCAL_ONLY", "0")
    assert "Bring in your account" not in _client().get("/setup").text


def test_no_bring_in_section_once_an_organisation_exists(home, tmp_path):
    _old_project(tmp_path, env=GOOD_ENV)
    assert _bring(_client(), tmp_path / "old project").status_code == 302
    assert "Bring in your account" not in _client().get("/setup").text


# ── success ────────────────────────────────────────────────────────────────────


def test_bringing_in_a_project_folder_brings_the_account_wikis_and_keys(home, tmp_path, caplog):
    project = _old_project(tmp_path, env=GOOD_ENV)
    before = _snapshot(project)
    c = _client()
    r = _bring(c, project)
    assert r.status_code == 302 and r.headers["location"] == "/login?brought_in=1"

    assert "Sign in with the email and password" in c.get(r.headers["location"]).text
    login = _login(c)
    assert login.status_code == 302 and login.headers["location"] == "/"
    assert "session_token" in login.cookies

    assert (home / "wikis" / "user-1" / "page.md").read_text() == "# Old page\nkept\n"
    assert (home / "files" / "report.txt").read_text() == "report"
    assert (home / "skills" / "mine" / "SKILL.md").read_text() == "my skill"
    assert (home / "workspace" / "note.md").read_text() == "workspace note"

    secrets = home / "secrets.env"
    assert (secrets.stat().st_mode & 0o777) == 0o600
    saved = secrets.read_text()
    assert f"ANTHILL_ENCRYPTION_KEY={OLD_KEY}" in saved and f"ANTHILL_JWT_SECRET={OLD_JWT}" in saved
    assert FRESH_KEY not in saved and "AWS_SECRET" not in saved
    assert os.environ["ANTHILL_ENCRYPTION_KEY"] == OLD_KEY

    s = app_mod._SessionFactory()
    stored = s.query(OrgSettings).first().org_lambda_tunnel_key_enc
    s.close()
    assert crypto.decrypt(stored) == STORED_SECRET

    assert _snapshot(project) == before
    assert not list(home.rglob(".bring-in-*"))
    assert OLD_KEY not in caplog.text and OLD_JWT not in caplog.text


def test_the_data_folder_alone_also_works(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    c = _client()
    r = _bring(c, project / "data")
    assert r.status_code == 302 and r.headers["location"] == "/login?brought_in=1"
    assert _login(c).status_code == 302
    assert (home / "workspace" / "note.md").exists()
    assert f"ANTHILL_ENCRYPTION_KEY={OLD_KEY}" in (home / "secrets.env").read_text()


def test_a_database_with_a_live_wal_file_is_read_through_it(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV, dispose=False)
    assert (project / "data" / "anthill.db-wal").exists()
    c = _client()
    assert _bring(c, project).status_code == 302
    assert _login(c).status_code == 302


def test_files_already_in_the_new_install_are_never_overwritten(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    (home / "skills" / "mine").mkdir(parents=True)
    (home / "skills" / "mine" / "SKILL.md").write_text("new install's own")
    (home / "skills" / "starter").mkdir()
    (home / "skills" / "starter" / "SKILL.md").write_text("starter")
    assert _bring(_client(), project).status_code == 302
    assert (home / "skills" / "mine" / "SKILL.md").read_text() == "new install's own"
    assert (home / "skills" / "starter" / "SKILL.md").read_text() == "starter"
    assert (home / "wikis" / "user-1" / "page.md").exists()


def _fail_at_the_last_step(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bring_in, "_swap_database", boom)


def test_a_failure_at_the_last_step_leaves_a_fresh_install_exactly_as_it_was(
    home, tmp_path, monkeypatch
):
    project = _old_project(tmp_path, env=GOOD_ENV)
    _fail_at_the_last_step(monkeypatch)
    r = _refused(home, _client(), project)
    assert "Nothing in this app was changed" in r.text
    assert not (home / "wikis" / "user-1").exists()
    assert "ANTHILL_ENCRYPTION_KEY=" + FRESH_KEY in (home / "secrets.env").read_text()


def test_a_failure_at_the_last_step_keeps_files_that_were_already_there(
    home, tmp_path, monkeypatch
):
    project = _old_project(tmp_path, env=GOOD_ENV)
    (home / "wikis" / "other").mkdir(parents=True)
    (home / "wikis" / "other" / "keep.md").write_text("already here")
    _fail_at_the_last_step(monkeypatch)
    _refused(home, _client(), project)
    assert (home / "wikis" / "other" / "keep.md").read_text() == "already here"
    assert not (home / "wikis" / "user-1").exists()


def test_other_names_in_the_old_env_file_are_not_copied(home, tmp_path):
    env = (
        GOOD_ENV
        + "# comment\nexport JINA_API_KEY='jina-value'\nOPENAI_API_KEY=nope\nGOOGLE_SEARCH_CX=\n"
    )
    project = _old_project(tmp_path, env=env)
    assert _bring(_client(), project).status_code == 302
    saved = (home / "secrets.env").read_text()
    assert "JINA_API_KEY=jina-value" in saved
    assert "OPENAI_API_KEY" not in saved and "GOOGLE_SEARCH_CX" not in saved


# ── no saved keys ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "env",
    [None, f"ANTHILL_JWT_SECRET={OLD_JWT}\n", "ANTHILL_ENCRYPTION_KEY=not-base64-of-32-bytes\n"],
)
def test_missing_keys_ask_first_and_then_continue_with_the_new_keys(home, tmp_path, env):
    project = _old_project(tmp_path, env=env)
    before = _snapshot(home)
    c = _client()
    ask = _bring(c, project)
    assert ask.status_code == 200
    assert "will need entering again" in ask.text and 'name="without_keys"' in ask.text
    assert _snapshot(home) == before and app_mod._db().query(Organization).count() == 0

    r = _bring(c, project, without_keys="1")
    assert r.status_code == 302 and r.headers["location"] == "/login?brought_in=keys"
    assert "need entering again" in c.get(r.headers["location"]).text
    assert _login(c).status_code == 302
    assert (home / "wikis" / "user-1" / "page.md").exists()
    assert f"ANTHILL_ENCRYPTION_KEY={FRESH_KEY}" in (home / "secrets.env").read_text()
    assert OLD_JWT not in ask.text


# ── refusals leave the new install exactly as it was ───────────────────────────


def _refused(home, c, folder, status=400, **extra):
    before = _snapshot(home)
    r = _bring(c, folder, **extra)
    assert r.status_code == status
    assert _snapshot(home) == before
    assert app_mod._db().query(Organization).count() == 0
    assert not list(home.rglob(".bring-in-*"))
    return r


def test_refuses_what_is_not_a_folder(home, tmp_path):
    c = _client()
    (tmp_path / "file.txt").write_text("x")
    assert "not a folder" in _refused(home, c, tmp_path / "file.txt").text
    assert "not a folder" in _refused(home, c, tmp_path / "missing").text
    assert "full path" in _refused(home, c, "relative/folder").text
    assert "Enter the folder" in _refused(home, c, "  ").text


def test_refuses_a_folder_with_no_anthill_database(home, tmp_path):
    (tmp_path / "empty").mkdir()
    assert "No Anthill account" in _refused(home, _client(), tmp_path / "empty").text


def test_refuses_a_sqlite_file_that_is_not_anthill(home, tmp_path):
    other = tmp_path / "other" / "data"
    other.mkdir(parents=True)
    with sqlite3.connect(other / "anthill.db") as con:
        con.execute("CREATE TABLE notes (a)")
    assert "not an Anthill database" in _refused(home, _client(), tmp_path / "other").text
    (other / "anthill.db").write_bytes(b"not a database at all" * 100)
    assert "not an Anthill database" in _refused(home, _client(), tmp_path / "other").text


def test_refuses_an_anthill_database_with_no_account(home, tmp_path):
    project = tmp_path / "noacct"
    eng = db_mod.get_engine(project / "data" / "anthill.db")
    db_mod.create_tables(eng)
    eng.dispose()
    assert "holds no account" in _refused(home, _client(), project).text


def test_refuses_the_folder_this_app_already_uses(home):
    (home / "wikis").mkdir()
    c = _client()
    assert "already uses" in _refused(home, c, home).text
    assert "already uses" in _refused(home, c, home / "wikis").text


def test_refuses_a_link_that_leaves_the_folder(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private")
    (project / "data" / "wikis" / "user-1" / "escape").symlink_to(outside)
    assert "outside it" in _refused(home, _client(), project).text


def test_refuses_a_linked_env_file_or_database(home, tmp_path):
    project = _old_project(tmp_path, env=None)
    stolen = tmp_path / "stolen.env"
    stolen.write_text(GOOD_ENV)
    (project / ".env").symlink_to(stolen)
    assert "outside it" in _refused(home, _client(), project).text
    (project / ".env").unlink()
    real = project / "data" / "anthill.db"
    moved = tmp_path / "moved.db"
    real.rename(moved)
    real.symlink_to(moved)
    assert "outside it" in _refused(home, _client(), project).text


def test_a_link_that_stays_inside_the_folder_is_skipped_not_followed(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    (project / "data" / "wikis" / "alias").symlink_to(project / "data" / "wikis" / "user-1")
    assert _bring(_client(), project).status_code == 302
    assert not (home / "wikis" / "alias").exists()
    assert (home / "wikis" / "user-1" / "page.md").exists()


def test_refuses_when_the_install_already_has_an_organisation(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    s = app_mod._SessionFactory()
    s.add(Organization(name="Mine", slug="mine"))
    s.commit()
    s.close()
    before = _snapshot(home)
    r = _bring(_client(), project)
    assert r.status_code == 403
    assert _snapshot(home) == before
    assert app_mod._db().query(User).count() == 0


@pytest.mark.parametrize(
    "client",
    [
        {"host": "anthill.example.com", "client": ("203.0.113.5", 50000)},
        {"host": "127.0.0.1", "client": ("203.0.113.5", 50000)},
        {"host": "anthill.example.com", "client": ("127.0.0.1", 50000)},
    ],
)
def test_refuses_a_request_that_does_not_come_from_this_machine(home, tmp_path, client):
    project = _old_project(tmp_path, env=GOOD_ENV)
    _refused(home, _client(**client), project, status=403)


def test_refuses_a_forwarded_request_and_a_cross_site_form(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    before = _snapshot(home)
    for headers in (
        {"X-Forwarded-For": "198.51.100.7"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ):
        r = _client().post(
            "/setup/bring-in",
            data={"folder": str(project)},
            headers=headers,
            follow_redirects=False,
        )
        assert r.status_code == 403
    assert _snapshot(home) == before


def test_refuses_on_a_server_that_is_not_marked_local(home, tmp_path, monkeypatch):
    project = _old_project(tmp_path, env=GOOD_ENV)
    monkeypatch.setenv("ANTHILL_LOCAL_ONLY", "0")
    _refused(home, _client(), project, status=403)


def test_a_folder_name_is_shown_back_escaped(home):
    r = _client().post("/setup/bring-in", data={"folder": '/"><script>alert(1)</script>'})
    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text


# ── pieces ─────────────────────────────────────────────────────────────────────


def test_env_parsing_keeps_only_the_listed_names():
    text = '# c\nexport ANTHILL_JWT_SECRET="a b"\nBAD LINE\nFOO=1\nMEILI_URL = http://x \nJINA_API_KEY=\n'
    assert bring_in._parse_env(text) == {"ANTHILL_JWT_SECRET": "a b", "MEILI_URL": "http://x"}
    assert bring_in._parse_env("FOO=1\n", None) == {"FOO": "1"}


# ── the key lives in the Keychain (key-location.json present) ─────────────────────


@pytest.fixture
def in_keychain(home, monkeypatch):
    """The fresh install keeps its key in a (fake) Keychain item, not in secrets.env or the environment."""
    from anthill import keystore

    (home / "secrets.env").unlink()
    for name in ("ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET", "ANTHILL_ALLOW_EPHEMERAL_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(crypto, "_KEY", None)
    monkeypatch.setattr(crypto, "_JWT_SECRET", None)
    backend = keystore.FakeBackend()
    keystore.set_backend(backend)
    keystore.ensure_ready()
    return backend, (home / "key-location.json").read_text(), keystore.data_key()


def test_bring_in_swaps_the_keychain_item_and_keeps_the_keys_out_of_the_files(
    home, tmp_path, in_keychain
):
    from anthill import keystore

    backend, marker_before, fresh_key = in_keychain
    project = _old_project(tmp_path, env=GOOD_ENV)
    r = _bring(_client(), project)
    assert r.status_code == 302 and r.headers["location"] == "/login?brought_in=1"
    assert keystore.data_key() == base64.b64decode(OLD_KEY) != fresh_key
    assert keystore.jwt_secret() == OLD_JWT
    assert (home / "key-location.json").read_text() != marker_before
    assert len(backend.items) == 1  # the previous item is removed once the bring-in worked
    assert (
        not (home / "secrets.env").exists()
        or "ANTHILL_ENCRYPTION_KEY" not in (home / "secrets.env").read_text()
    )
    assert "ANTHILL_ENCRYPTION_KEY" not in os.environ
    keystore.reset()
    keystore.ensure_ready()  # a restart reads the new item and the stored value opens
    s = app_mod._SessionFactory()
    stored = s.query(OrgSettings).first().org_lambda_tunnel_key_enc
    s.close()
    assert crypto.decrypt(stored) == STORED_SECRET


def test_a_failure_after_the_new_item_is_made_restores_the_old_marker_and_removes_it(
    home, tmp_path, monkeypatch, in_keychain
):
    from anthill import keystore

    backend, marker_before, fresh_key = in_keychain
    items_before = dict(backend.items)
    project = _old_project(tmp_path, env=GOOD_ENV)
    monkeypatch.setattr(
        bring_in, "_swap_database", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    r = _bring(_client(), project)
    assert r.status_code == 400 and "Nothing in this app was changed" in r.text
    assert (home / "key-location.json").read_text() == marker_before
    assert backend.items == items_before and keystore.data_key() == fresh_key


def test_a_key_that_does_not_open_the_earlier_copys_values_is_refused_before_any_change(
    home, tmp_path, in_keychain
):
    backend, marker_before, _ = in_keychain
    wrong = f"ANTHILL_JWT_SECRET={OLD_JWT}\nANTHILL_ENCRYPTION_KEY={FRESH_KEY}\n"
    project = _old_project(tmp_path, env=wrong)
    before = _snapshot(home)
    r = _bring(_client(), project)
    assert r.status_code == 400 and "does not open its saved passwords" in r.text
    assert _snapshot(home) == before and (home / "key-location.json").read_text() == marker_before
    assert len(backend.items) == 1


def test_an_earlier_copy_with_no_stored_values_needs_no_matching_key(home, tmp_path):
    project = _old_project(tmp_path, env=GOOD_ENV)
    with sqlite3.connect(project / "data" / "anthill.db") as con:
        con.execute("UPDATE org_settings SET org_lambda_tunnel_key_enc = ''")
    other = f"ANTHILL_JWT_SECRET={OLD_JWT}\nANTHILL_ENCRYPTION_KEY={FRESH_KEY}\n"
    (project / ".env").write_text(other)
    assert _bring(_client(), project).status_code == 302
