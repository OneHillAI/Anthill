"""Start-up glue for the key layer: the database probe and checker, the plain refusal at the entry points, the
desktop's key file, and the backup page's key status. Model-free; the Keychain is a FakeBackend."""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anthill import desktop, keystore
from anthill.keystore import FakeBackend
from anthill.web import crypto, key_startup

# The real function, imported at collection time (conftest turns the module attribute into a no-op per test).
real_ensure_or_exit = key_startup.ensure_or_exit

KEY = b"k" * 32
OTHER = b"x" * 32


def _db(
    path: Path, *, token: str | None = None, members: object = None, table: bool = True
) -> Path:
    with sqlite3.connect(path) as con:
        if table:
            con.execute(
                "CREATE TABLE org_settings (id INTEGER, org_model_key_enc TEXT, org_council_members TEXT)"
            )
            con.execute(
                "INSERT INTO org_settings VALUES (1, ?, ?)",
                (token or "", json.dumps(members) if members is not None else "[]"),
            )
    return path


def _enc(key: bytes, text: str = "secret") -> str:
    return _with_key(key, lambda: crypto.encrypt(text))


def _with_key(key: bytes, fn):
    saved = crypto._KEY
    crypto._KEY = key
    try:
        return fn()
    finally:
        crypto._KEY = saved


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "data"
    h.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHILL_HOME", str(h))
    monkeypatch.setenv("ANTHILL_DB", str(h / "anthill.db"))
    for name in ("ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET", "ANTHILL_ALLOW_EPHEMERAL_KEY"):
        monkeypatch.delenv(name, raising=False)
    keystore.set_backend(FakeBackend())
    key_startup.register()
    return h


# -- the probe and the checker ----------------------------------------------------------


def test_the_probe_sees_a_stored_value_in_a_column_or_a_council_copy(tmp_path):
    assert key_startup.database_holds_values(tmp_path / "missing.db") is False
    assert key_startup.database_holds_values(_db(tmp_path / "empty.db", table=False)) is False
    assert key_startup.database_holds_values(_db(tmp_path / "blank.db")) is False
    assert key_startup.database_holds_values(_db(tmp_path / "col.db", token=_enc(KEY))) is True
    member = [{"name": "m", "provider_config": {"hf_token_enc": _enc(KEY)}}]
    assert key_startup.database_holds_values(_db(tmp_path / "copy.db", members=member)) is True
    junk = [{"model_key_enc": ""}, "not a dict", {"provider_config": 5}]
    assert key_startup.database_holds_values(_db(tmp_path / "junk.db", members=junk)) is False


def test_an_unreadable_database_is_an_error_never_no_values(tmp_path, monkeypatch):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    with pytest.raises(keystore.ValuesUnreadable):
        key_startup.database_holds_values(bad)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    good = _db(tmp_path / "good.db", token=_enc(KEY))
    monkeypatch.setattr(key_startup.sqlite3, "connect", locked)
    with pytest.raises(keystore.ValuesUnreadable):
        key_startup.database_holds_values(good)


def test_the_checker_needs_one_stored_value_to_open(home):
    db = _db(home / "anthill.db", token=_enc(KEY))
    assert key_startup.check(KEY) is True
    assert key_startup.check(OTHER) is False
    assert key_startup.verify_every_value(KEY, db) is True
    assert key_startup.verify_every_value(OTHER, db) is False
    empty = _db(home / "empty.db", table=False)
    assert key_startup.verify_every_value(OTHER, empty) is True  # no stored values: nothing to open


def test_every_value_must_open_for_bring_in(tmp_path):
    members = [{"model_key_enc": _enc(OTHER)}]
    db = _db(tmp_path / "two.db", token=_enc(KEY), members=members)
    assert key_startup.verify_every_value(KEY, db) is False


def test_start_sh_probe_command_exit_codes(tmp_path):
    def run(path):
        return subprocess.run(
            [sys.executable, "-m", "anthill.web.key_startup", "has-values", str(path)],
            capture_output=True,
        ).returncode

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"junk" * 500)
    assert run(tmp_path / "none.db") == 0
    assert run(_db(tmp_path / "v.db", token=_enc(KEY))) == 1
    assert run(bad) == 2
    assert key_startup.main(["nonsense"]) == 64


# -- the plain refusal ------------------------------------------------------------------------


def test_a_missing_item_stops_the_start_in_plain_words_with_no_traceback(home, capsys, monkeypatch):
    keystore.reset()
    keystore.ensure_ready()
    keystore._backend.items.clear()
    monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    keystore.reset()
    with pytest.raises(SystemExit) as stop:
        real_ensure_or_exit()
    err = capsys.readouterr().err
    assert stop.value.code == 1
    assert "Anthill did not start" in err and "no new key was made" in err
    assert "Traceback" not in err and not keystore._backend.items


def test_stored_values_and_no_key_stop_the_start_even_with_the_dev_switch(
    home, capsys, monkeypatch
):
    _db(home / "anthill.db", token=_enc(KEY))
    monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    keystore.reset()
    with pytest.raises(SystemExit) as stop:
        real_ensure_or_exit()
    assert stop.value.code == 1 and "will not make a new key" in capsys.readouterr().err
    assert not (home / "key-location.json").exists() and keystore._backend.items == {}


def test_an_unreadable_database_stops_the_start_in_plain_words(home, capsys):
    (home / "anthill.db").write_bytes(b"junk" * 500)
    keystore.reset()
    with pytest.raises(SystemExit):
        real_ensure_or_exit()
    err = capsys.readouterr().err
    assert "could not read its database" in err and "Traceback" not in err


def test_a_good_start_prints_nothing_and_notes_are_shown(home, capsys, monkeypatch):
    keystore.reset()
    real_ensure_or_exit()
    assert capsys.readouterr().err == ""
    monkeypatch.setattr(keystore, "ensure_ready", lambda: ["one plain note"])
    real_ensure_or_exit()
    assert "one plain note" in capsys.readouterr().err


def test_the_migration_checks_the_legacy_key_against_the_real_database(home, monkeypatch):
    _db(home / "anthill.db", token=_enc(KEY))
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(OTHER).decode())
    notes = keystore.ensure_ready()
    assert (
        "opens none of the stored passwords" in notes[0]
        and not (home / "key-location.json").exists()
    )
    keystore.reset()
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(KEY).decode())
    assert keystore.ensure_ready() == [] and (home / "key-location.json").exists()


def test_any_other_start_stops_in_the_startup_handler(home, monkeypatch, capsys):
    from anthill.web import app as app_mod

    monkeypatch.setattr(key_startup, "ensure_or_exit", real_ensure_or_exit)
    _db(home / "anthill.db", token=_enc(KEY))
    keystore.reset()
    with pytest.raises(SystemExit):
        app_mod._startup()
    assert "Anthill did not start" in capsys.readouterr().err


# -- the desktop's key file -----------------------------------------------------------------------


def test_a_fresh_mac_install_writes_no_key_file(home):
    desktop._ensure_secrets(home)
    assert not (home / "secrets.env").exists()


def test_a_marked_install_never_gets_a_key_file(home):
    keystore.reset()
    keystore.ensure_ready()
    keystore._backend._available = False  # even where the Keychain cannot be asked
    desktop._ensure_secrets(home)
    assert not (home / "secrets.env").exists()


def test_without_a_keychain_a_fresh_install_gets_a_key_file_as_before(home, monkeypatch):
    keystore.set_backend(FakeBackend(available=False))
    desktop._ensure_secrets(home)
    saved = (home / "secrets.env").read_text()
    assert "ANTHILL_ENCRYPTION_KEY=" in saved and "ANTHILL_JWT_SECRET=" in saved
    assert ((home / "secrets.env").stat().st_mode & 0o777) == 0o600
    desktop._ensure_secrets(home)
    assert (home / "secrets.env").read_text() == saved


def test_without_a_keychain_a_missing_key_over_stored_values_is_refused(home, capsys):
    keystore.set_backend(FakeBackend(available=False))
    _db(home / "anthill.db", token=_enc(KEY))
    with pytest.raises(SystemExit) as stop:
        desktop._ensure_secrets(home)
    err = capsys.readouterr().err
    assert stop.value.code == 1 and "no new key was made" in err and "Traceback" not in err
    assert not (home / "secrets.env").exists()


def test_the_entry_points_call_the_key_start(monkeypatch):
    calls = []
    monkeypatch.setattr(key_startup, "ensure_or_exit", lambda: calls.append(1))
    desktop.ensure_key_or_exit()
    assert calls == [1]


# -- the backup page ----------------------------------------------------------------------------------


def test_the_backup_page_shows_one_status_line_one_note_and_the_disk_sentence(home, monkeypatch):
    from anthill.web import app as app_mod

    keystore.reset()
    keystore.ensure_ready()
    monkeypatch.setattr(keystore, "_disk_encryption", lambda: "off")
    monkeypatch.setattr(app_mod, "install_scope_allowed", lambda *a, **k: True)
    html = _backup_html(app_mod)
    assert "The key is stored in this Mac&#39;s Keychain, not in the data folder." in html
    assert (
        "Backups and copies of the data folder made before the key moved still open every saved password."
        in html
    )
    assert "Delete older backups and old copies of" in html
    assert "Disk encryption (FileVault) is off" in html
    assert (
        "does not contain the encryption key" in html
        and "includes <code>secrets.env</code>" not in html
    )


def test_the_backup_page_says_plainly_when_the_key_is_in_a_file(home, monkeypatch):
    from anthill.web import app as app_mod

    keystore.set_backend(FakeBackend(available=False))
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(KEY).decode())
    keystore.reset()
    keystore.ensure_ready()
    monkeypatch.setattr(keystore, "_disk_encryption", lambda: "unknown")
    monkeypatch.setattr(app_mod, "install_scope_allowed", lambda *a, **k: True)
    html = _backup_html(app_mod)
    assert (
        "It is not protected: anyone with a copy of your data folder can read your saved passwords."
        in html
    )
    assert (
        "could not tell whether disk encryption is on" in html
        and "includes <code>secrets.env</code>" in html
    )


def _backup_html(app_mod) -> str:
    from anthill.web import db as db_mod

    eng = db_mod.get_engine()
    db_mod.create_tables(eng)
    from sqlalchemy.orm import sessionmaker

    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        s = app_mod._SessionFactory()
        org = db_mod.Organization(name="O", slug="o")
        s.add(org)
        s.flush()
        s.add(db_mod.OrgSettings(org_id=org.id, deployment_topology="solo"))
        s.commit()
        oid = org.id
        s.close()
        app_mod.app.dependency_overrides[app_mod._require_admin] = lambda: {
            "sub": "1",
            "org": oid,
            "role": "admin",
        }
        return TestClient(app_mod.app).get("/backup").text
    finally:
        app_mod.app.dependency_overrides.pop(app_mod._require_admin, None)
        eng.dispose()
        app_mod._engine = None
        app_mod._SessionFactory = None
