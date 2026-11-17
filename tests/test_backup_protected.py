"""Protected backups (anthill/backup.py, anthill/backup_crypto.py). Spec: docs/specs/encryption-at-rest-phase1.md.

The whole archive is encrypted; the key travels inside it; restore verifies everything before touching the
install and puts the old state back on failure. Model-free: the Keychain is a FakeBackend, keys are test
values, and every "machine" is a temp folder."""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import struct
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typer.testing import CliRunner

from anthill import backup as bk
from anthill import backup_crypto as bc
from anthill import keystore

PHRASE = "a-long-test-passphrase"
DB_MARK = "database-marker-text-0451"
WIKI_MARK = "wiki-marker-text-9172"
STORED_VALUE = "the-stored-provider-key"


def enc(key: bytes, text: str = STORED_VALUE) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, text.encode(), None)).decode()


def make_install(tmp_path, monkeypatch, name, *, keychain, key=None, values=True):
    """One machine: its own data folder, its own key (in a fake Keychain, or in secrets.env), a database with a
    stored value encrypted under that key, a wiki page, a file and a skill."""
    home = tmp_path / name
    home.mkdir()
    for var, sub in (
        ("ANTHILL_HOME", ""),
        ("ANTHILL_DB", "anthill.db"),
        ("ANTHILL_WORKSPACE", "workspace"),
        ("ANTHILL_FILES_DIR", "files"),
        ("ANTHILL_SKILLS_DIR", "skills"),
        ("ANTHILL_WIKI_ROOT", "wikis"),
        ("ANTHILL_ORG_WIKI", "org-wiki"),
        ("ANTHILL_ORG_WIKIS", "org-wikis"),
    ):
        monkeypatch.setenv(var, str(home / sub) if sub else str(home))
    for var in ("ANTHILL_ALLOW_EPHEMERAL_KEY", "ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    backend = keystore.FakeBackend(available=keychain)
    keystore.set_backend(backend)
    if keychain:
        keystore.ensure_ready()
        key = keystore.data_key()
    else:
        key = key or os.urandom(32)
        monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(key).decode())
        monkeypatch.setenv("ANTHILL_JWT_SECRET", f"jwt-of-{name}")
        (home / "secrets.env").write_text(
            f"ANTHILL_ENCRYPTION_KEY={base64.b64encode(key).decode()}\nANTHILL_JWT_SECRET=jwt-of-{name}\nKEEP=1\n"
        )
        keystore.reset()
    with (home / "secrets.env").open("a") as fh:  # an optional key a bring-in copied here
        fh.write(f"OPTIONAL_KEY=opt-{name}\n")
    with sqlite3.connect(home / "anthill.db") as con:
        con.execute(
            "CREATE TABLE org_settings (id INTEGER, org_model_key_enc TEXT, org_council_members TEXT)"
        )
        con.execute("CREATE TABLE notes (t TEXT)")
        con.execute("INSERT INTO notes VALUES (?)", (DB_MARK + "-" + name,))
        con.execute("INSERT INTO org_settings VALUES (1, ?, '[]')", (enc(key) if values else "",))
    (home / "wikis" / "user-1").mkdir(parents=True)
    (home / "wikis" / "user-1" / "p.md").write_text(WIKI_MARK + "-" + name)
    (home / "files").mkdir()
    (home / "files" / "a.txt").write_text("file-" + name)
    (home / "skills").mkdir()
    (home / "skills" / "s.md").write_text("skill-" + name)
    return SimpleNamespace(home=home, key=key, backend=backend, name=name)


def tree(home: Path) -> dict[str, bytes]:
    """Everything in a data folder, for 'nothing was touched' checks (backups and staging excluded)."""
    skip = {"anthill.db-wal", "anthill.db-shm"}
    return {
        str(p.relative_to(home)): p.read_bytes()
        for p in sorted(home.rglob("*"))
        if p.is_file() and p.name not in skip and "backups" not in p.parts
    }


def note(home: Path) -> str:
    with sqlite3.connect(home / "anthill.db") as con:
        return con.execute("SELECT t FROM notes").fetchone()[0]


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Machine A makes a backup; machine B (another key) will restore it."""
    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    archive = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    b = make_install(tmp_path, monkeypatch, "b", keychain=True)
    return a, b, archive


# -- what is in the file ----------------------------------------------------------------


def test_the_archive_shows_nothing_without_the_passphrase(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    path = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    raw = path.read_bytes()
    assert raw.startswith(b"ANTHBAK") and (path.stat().st_mode & 0o777) == 0o600
    needles = [
        a.key,
        base64.b64encode(a.key),
        keystore.jwt_secret().encode(),
        b"secrets.env",
        b"key-export",
        b"key-location",
        DB_MARK.encode(),
        WIKI_MARK.encode(),
        b"SQLite format",
        b"manifest.json",
        STORED_VALUE.encode(),
        b"opt-a",
        b"OPTIONAL_KEY",
    ]
    for needle in needles:
        assert needle not in raw
    assert not list(a.home.glob("**/*.partial"))


def test_with_the_passphrase_it_holds_the_key_the_data_and_no_key_files(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    (a.home / "files" / "secrets.env").write_text("ANTHILL_ENCRYPTION_KEY=planted")
    path = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    with open(path, "rb") as fh:
        with tarfile.open(fileobj=bc.DecryptingReader(fh, PHRASE), mode="r|gz") as tar:
            names = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    assert not [
        n for n in names if n.endswith("secrets.env") or "key-location" in n or "ollama" in n
    ]
    export = json.loads(names["data/key-export.json"])
    assert export["env"] == {"OPTIONAL_KEY": "opt-a", "KEEP": "1"} or export["env"] == {
        "OPTIONAL_KEY": "opt-a"
    }
    assert (
        base64.b64decode(export["data_key"]) == a.key
        and export["jwt_secret"] == keystore.jwt_secret()
    )
    manifest = json.loads(names["manifest.json"])
    assert manifest["protected"] is True and "secrets.env" not in manifest["note"]
    assert bk.read_manifest(path, PHRASE)["anthill_backup_format"] == 2
    with pytest.raises(bk.PassphraseNeeded):
        bk.read_manifest(path)


def test_a_new_backup_needs_a_passphrase_of_twelve_characters(tmp_path, monkeypatch):
    make_install(tmp_path, monkeypatch, "a", keychain=True)
    for bad in ("", "   ", "short", "elevenchars"):
        with pytest.raises(bk.PassphraseTooShort, match="at least 12 characters"):
            bk.create_backup(tmp_path / "x.anthillbak", passphrase=bad)
    assert not (tmp_path / "x.anthillbak").exists()
    bk.create_backup(tmp_path / "x.anthillbak", passphrase="exactly12chr")


# -- the container: wrong passphrase, damage, hostile headers ------------------------------------


def test_wrong_passphrase_and_damage_fail_closed_and_touch_nothing(two, tmp_path):
    _a, b, archive = two
    before = tree(b.home)
    keys_before = dict(b.backend.items)
    raw = archive.read_bytes()
    cases = {
        "wrong": (raw, "not the right passphrase"),
        "flip": (raw[:200] + bytes([raw[200] ^ 1]) + raw[201:], PHRASE),
        "last": (raw[:-1] + bytes([raw[-1] ^ 1]), PHRASE),
        "cut": (raw[: len(raw) // 2], PHRASE),
        "extra": (raw + b"x" * 40, PHRASE),
        "header": (raw[:12] + b"\x00" + raw[13:], PHRASE),
    }
    for label, (data, phrase) in cases.items():
        broken = tmp_path / f"{label}.anthillbak"
        broken.write_bytes(data)
        with pytest.raises(bk.BackupError):
            bk.restore_backup(broken, passphrase=phrase)
        assert tree(b.home) == before and b.backend.items == keys_before, label
    with pytest.raises(bc.WrongPassphrase):
        bk.restore_backup(archive, passphrase="not the right passphrase")
    assert not list(b.home.glob(".restore-staging-*"))


def test_a_chunk_cannot_be_moved_or_dropped(tmp_path):
    out = io.BytesIO()
    w = bc.EncryptingWriter(out, PHRASE, chunk_size=4096)
    w.write(os.urandom(4096 * 3))
    w.close()
    data, head, sealed = out.getvalue(), 15 + 16 + 12, 4096 + 16
    c = [data[head + i * sealed : head + (i + 1) * sealed] for i in range(3)]
    assert len(data) == head + 3 * sealed
    ok = bc.DecryptingReader(io.BytesIO(data), PHRASE)
    ok.drain()
    for bad in (
        data[:head] + c[1] + c[0] + c[2],  # moved
        data[:head] + c[0] + c[2] + c[2],  # repeated
        data[:head] + c[0] + c[1],  # the final chunk dropped (cut exactly between two chunks)
        data[:head] + c[0] + c[1] + c[2] + c[2],  # extra
    ):
        with pytest.raises(bc.BackupCryptoError):
            bc.DecryptingReader(io.BytesIO(bad), PHRASE).drain()


def _header(n, r, p, salt_len=16, chunk=1 << 20):
    return (
        struct.pack(">7sBIBBB", b"ANTHBAK", 1, n, r, p, salt_len)
        + b"s" * salt_len
        + struct.pack(">I8s", chunk, b"p" * 8)
        + b"\x00" * 64
    )


@pytest.mark.parametrize(
    ("n", "r", "p"),
    [
        (2**18, 16, 1),  # 512 MiB
        (2**18, 8, 4),  # 256 MiB but far too much work
        (2**17, 16, 4),  # work 2**23
        (2**20, 8, 1),  # n over its own cap
        (2**14 + 1, 8, 1),  # not a power of two
        (2**13, 8, 1),  # too cheap to protect anything
        (2**14, 0, 1),
        (2**14, 8, 0),
        (2**14, 8, 255),
    ],
)
def test_a_hostile_header_is_refused_before_any_key_is_derived(monkeypatch, n, r, p):
    def never(*a, **k):
        raise AssertionError("scrypt must not run for this header")

    monkeypatch.setattr(bc, "Scrypt", never)
    with pytest.raises(bc.BackupCryptoError, match="not accepted"):
        bc.DecryptingReader(io.BytesIO(_header(n, r, p)), PHRASE)


def test_the_default_cost_is_accepted_and_other_header_fields_are_capped(monkeypatch):
    monkeypatch.setattr(bc, "SCRYPT_N", 131072)
    out = io.BytesIO()
    w = bc.EncryptingWriter(out, PHRASE)
    w.write(b"hello")
    w.close()
    reader = bc.DecryptingReader(io.BytesIO(out.getvalue()), PHRASE)
    assert reader.read(5) == b"hello"
    with pytest.raises(bc.BackupCryptoError):
        bc.DecryptingReader(io.BytesIO(_header(2**14, 8, 1, salt_len=200)), PHRASE)
    with pytest.raises(bc.BackupCryptoError):
        bc.DecryptingReader(io.BytesIO(_header(2**14, 8, 1, chunk=2**30)), PHRASE)
    with pytest.raises(bc.NotProtectedBackup):
        bc.DecryptingReader(io.BytesIO(b"\x1f\x8b" + b"x" * 80), PHRASE)


# -- restore: the key travels, the checks come first --------------------------------------------


def test_restore_on_another_mac_swaps_the_key_item_and_keeps_the_old_one_until_it_worked(two):
    a, b, archive = two
    old_items = set(b.backend.items)
    old_key = b.key
    result = bk.restore_backup(archive, passphrase=PHRASE)
    assert result.protected and result.restart_needed and result.key_replaced
    assert {"db", "wikis", "files", "skills", "keys"} <= set(result.restored)
    assert note(b.home) == DB_MARK + "-a"
    env = (
        b.home / "secrets.env"
    ).read_text()  # the optional lines come back; the key pair is not a file
    assert (
        "OPTIONAL_KEY=opt-a" in env and "opt-b" not in env and "ANTHILL_ENCRYPTION_KEY" not in env
    )
    assert keystore.data_key() == a.key != old_key
    assert set(b.backend.items).isdisjoint(old_items) and len(b.backend.items) == 1
    keystore.reset()
    keystore.ensure_ready()  # a restart reads the new item
    assert keystore.data_key() == a.key
    assert result.safety_backup and result.safety_backup.exists()
    assert (
        b.home / "anthill.db.before-restore-"
    ).parent == b.home and result.db_moved_aside.exists()
    assert not list(b.home.glob(".restore-staging-*"))
    # the safety copy is protected with the same passphrase and holds the previous key
    with open(result.safety_backup, "rb") as fh:
        with tarfile.open(fileobj=bc.DecryptingReader(fh, PHRASE), mode="r|gz") as tar:
            export = json.loads(
                next(tar.extractfile(m).read() for m in tar if m.name.endswith("key-export.json"))
            )
    assert base64.b64decode(export["data_key"]) == old_key


def test_the_same_key_needs_no_swap(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    archive = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    items = dict(a.backend.items)
    result = bk.restore_backup(archive, passphrase=PHRASE)
    assert not result.key_replaced and a.backend.items == items


def test_a_lost_keychain_item_is_recovered_from_a_backup(two):
    a, b, archive = two
    b.backend.items.clear()  # the Keychain item is gone; the marker still points at it
    keystore.reset()
    with pytest.raises(keystore.KeyUnavailable):
        keystore.data_key()
    result = bk.restore_backup(archive, passphrase=PHRASE)
    assert result.key_replaced and result.safety_backup is None
    assert any("no safety copy was made" in w for w in result.warnings)
    keystore.reset()
    keystore.ensure_ready()
    assert keystore.data_key() == a.key and note(b.home) == DB_MARK + "-a"


def test_without_a_keychain_the_key_lines_go_into_secrets_env_and_the_old_file_is_kept(
    tmp_path, monkeypatch
):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    archive = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    b = make_install(tmp_path, monkeypatch, "b", keychain=False)
    result = bk.restore_backup(archive, passphrase=PHRASE)
    text = (b.home / "secrets.env").read_text()
    assert base64.b64encode(a.key).decode() in text and "KEEP=1" in text
    assert (
        result.key_moved_aside
        and result.key_moved_aside.read_text().count("ANTHILL_ENCRYPTION_KEY") == 1
    )
    assert base64.b64encode(b.key).decode() not in text and "jwt-of-a" in text


def test_a_key_that_does_not_open_the_stored_values_refuses_before_anything_changes(
    tmp_path, monkeypatch
):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    with sqlite3.connect(a.home / "anthill.db") as con:  # a value made under some other key
        con.execute("UPDATE org_settings SET org_model_key_enc = ?", (enc(os.urandom(32)),))
    archive = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    b = make_install(tmp_path, monkeypatch, "b", keychain=False)
    before = tree(b.home)
    with pytest.raises(bk.BackupError, match="does not open every saved password"):
        bk.restore_backup(archive, passphrase=PHRASE)
    assert tree(b.home) == before and not list(b.home.glob("backups/*"))
    knowingly = bk.restore_backup(archive, passphrase=PHRASE, allow_without_key=True)
    assert "db" in knowingly.restored and any("will not open" in w for w in knowingly.warnings)


def test_too_many_values_to_check_is_a_plain_refusal(two, monkeypatch):
    _a, b, archive = two
    monkeypatch.setattr("anthill.stored_values.CAP", 0)
    before = tree(b.home)
    with pytest.raises(bk.BackupError, match="too many to check"):
        bk.restore_backup(archive, passphrase=PHRASE)
    assert tree(b.home) == before


def test_a_failure_while_putting_data_in_place_puts_everything_back_and_keeps_the_safety_copy(
    two, monkeypatch
):
    _a, b, archive = two
    before, items, key = tree(b.home), dict(b.backend.items), b.key
    real = bk._Undo.place
    calls = {"n": 0}

    def flaky(self, staged, dest):
        calls["n"] += 1
        if calls["n"] == 3:  # after the database and one tree are in place
            raise OSError("disk full")
        return real(self, staged, dest)

    monkeypatch.setattr(bk._Undo, "place", flaky)
    with pytest.raises(OSError):
        bk.restore_backup(archive, passphrase=PHRASE)
    assert {k: v for k, v in tree(b.home).items() if "before-restore" not in k} == before
    assert (
        b.backend.items == items and keystore.data_key() == key and note(b.home) == DB_MARK + "-b"
    )
    assert list((b.home / "backups").glob("pre-restore-*.anthillbak"))  # the way back is kept
    assert not list(b.home.glob(".restore-staging-*"))


# -- what main already guarded: hostile archives, file modes ------------------------------------


def hostile_archive(path: Path, a, entries) -> Path:
    """A protected archive made by hand: A's database and key plus the given (TarInfo, bytes|None) entries."""
    manifest = json.dumps({"anthill_backup_format": 2, "includes": ["db"]}).encode()
    export = json.dumps(
        {"version": 1, "data_key": base64.b64encode(a.key).decode(), "jwt_secret": "j", "env": {}}
    ).encode()
    with open(path, "wb") as fh:
        writer = bc.EncryptingWriter(fh, PHRASE)
        with tarfile.open(fileobj=writer, mode="w|gz") as tar:
            for name, data in (("manifest.json", manifest), ("data/key-export.json", export)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            db = (a.home / "anthill.db").read_bytes()
            info = tarfile.TarInfo("data/anthill.db")
            info.size = len(db)
            tar.addfile(info, io.BytesIO(db))
            for info, data in entries:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
        writer.close()
    return path


def _entry(name, data=b"x", **fields):
    info = tarfile.TarInfo(name)
    for key, value in fields.items():
        setattr(info, key, value)
    if "type" not in fields:
        info.size = len(data)
    return info, data


def test_a_hostile_archive_cannot_write_outside_the_data_folder_or_plant_links(two, tmp_path):
    a, b, _ = two
    outside = tmp_path / "outside"
    outside.mkdir()
    entries = [
        _entry("data/files/../../escape.txt"),
        _entry("/abs-escape.txt"),
        _entry("data/files/sneaky/../../../escape2.txt"),
        _entry("data/wikis/link", None, type=tarfile.SYMTYPE, linkname=str(outside)),
        _entry("data/files/hard", None, type=tarfile.LNKTYPE, linkname="data/anthill.db"),
        _entry("data/files/dev", None, type=tarfile.CHRTYPE),
        _entry("data/files/fine.txt", b"kept"),
    ]
    archive = hostile_archive(tmp_path / "evil.anthillbak", a, entries)
    bk.restore_backup(archive, passphrase=PHRASE)
    assert not list(tmp_path.glob("**/escape*.txt")) and not (tmp_path / "abs-escape.txt").exists()
    assert not (b.home / "wikis" / "link").exists() and not (b.home / "wikis" / "link").is_symlink()
    assert not (b.home / "files" / "hard").exists() and not (b.home / "files" / "dev").exists()
    assert (b.home / "files" / "fine.txt").read_bytes() == b"kept" and list(outside.iterdir()) == []


def test_a_file_listed_twice_or_too_much_data_is_refused_before_anything_changes(
    two, tmp_path, monkeypatch
):
    a, b, _ = two
    before = tree(b.home)
    twice = hostile_archive(
        tmp_path / "twice.anthillbak",
        a,
        [_entry("data/files/x", b"1"), _entry("data/files/x", b"2")],
    )
    with pytest.raises(bk.BackupError, match="lists the same file twice"):
        bk.restore_backup(twice, passphrase=PHRASE)
    big = hostile_archive(tmp_path / "big.anthillbak", a, [_entry("data/files/x", b"1" * 5000)])
    monkeypatch.setattr(bk.shutil, "disk_usage", lambda p: SimpleNamespace(free=100))
    with pytest.raises(bk.BackupError, match="larger than this computer has room"):
        bk.restore_backup(big, passphrase=PHRASE)
    assert tree(b.home) == before and not list(b.home.glob(".restore-staging-*"))


def test_backup_restore_and_snapshot_files_are_private(two):
    _a, b, archive = two
    result = bk.restore_backup(archive, passphrase=PHRASE)
    mode = lambda path: path.stat().st_mode & 0o777  # noqa: E731
    assert mode(b.home / "anthill.db") == 0o600
    assert mode(archive) == 0o600 and mode(result.safety_backup) == 0o600
    snap = bk.snapshot_db()
    assert mode(snap) == 0o600 and mode(snap.parent) == 0o700


def test_a_restore_without_a_keychain_writes_secrets_env_privately(tmp_path, monkeypatch):
    make_install(tmp_path, monkeypatch, "a", keychain=False)
    archive = bk.create_backup(tmp_path / "a.anthillbak", passphrase=PHRASE).path
    b = make_install(tmp_path, monkeypatch, "b", keychain=False)
    (b.home / "secrets.env").chmod(0o644)
    bk.restore_backup(archive, passphrase=PHRASE)
    assert (b.home / "secrets.env").stat().st_mode & 0o777 == 0o600


# -- older plain backups -------------------------------------------------------------------------


def plain_archive(path: Path, home: Path, *, key: bytes | None, db: Path | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tar:

        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        add("manifest.json", json.dumps({"anthill_backup_format": 1, "includes": ["db"]}).encode())
        add("data/anthill.db", (db or home / "anthill.db").read_bytes())
        if key:
            line = f"ANTHILL_ENCRYPTION_KEY={base64.b64encode(key).decode()}\nANTHILL_JWT_SECRET=old-jwt\nFROM_PLAIN=1\n"
            add("data/secrets.env", line.encode())
    return path


def test_an_old_plain_backup_with_a_key_restores_with_a_plain_warning(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    old = plain_archive(tmp_path / "old.tar.gz", a.home, key=a.key)
    b = make_install(tmp_path, monkeypatch, "b", keychain=True)
    result = bk.restore_backup(old, passphrase=PHRASE)
    assert not result.protected and any("not protected" in w for w in result.warnings)
    assert keystore.data_key() == a.key and note(b.home) == DB_MARK + "-a"
    assert "FROM_PLAIN=1" in (b.home / "secrets.env").read_text()  # as main restored the whole file


def test_an_old_plain_backup_without_a_key_is_refused_unless_allowed(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    old = plain_archive(tmp_path / "old.tar.gz", a.home, key=None)
    b = make_install(tmp_path, monkeypatch, "b", keychain=False)
    before = tree(b.home)
    with pytest.raises(bk.BackupError, match="no key"):
        bk.restore_backup(old, passphrase="x")  # restore does not enforce the 12 characters
    assert tree(b.home) == before
    result = bk.restore_backup(old, passphrase="x", allow_without_key=True)
    assert "db" in result.restored and any("will not open" in w for w in result.warnings)
    empty = make_install(tmp_path, monkeypatch, "c", keychain=False, values=False)
    quiet = plain_archive(tmp_path / "q.tar.gz", empty.home, key=None)
    assert (
        "db" in bk.restore_backup(quiet, passphrase="x").restored
    )  # nothing stored, nothing to lose


def test_a_passphrase_is_needed_to_restore_and_for_the_safety_copy(two):
    _a, _b, archive = two
    with pytest.raises(bk.PassphraseNeeded):
        bk.restore_backup(archive)
    with pytest.raises(bk.PassphraseNeeded):
        bk.restore_backup(archive, passphrase="   ")


# -- staging folders -------------------------------------------------------------------------------


def test_a_crashed_restore_leaves_nothing_behind_after_the_next_start(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    name = ".restore-staging-20260101-000000-abc123"
    stale = a.home / name
    (stale / "data").mkdir(parents=True)
    (stale / "data" / "x").write_text("leftover")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    young = a.home / ".restore-staging-20260101-000001-abc124"
    young.mkdir()
    in_use = a.home / ".restore-staging-20260101-000002-abc125"
    in_use.mkdir()
    os.utime(in_use, (old, old))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep").write_text("k")
    link = a.home / ".restore-staging-20260101-000003-abc126"
    link.symlink_to(outside)
    odd_name = a.home / ".restore-staging-notes"
    odd_name.mkdir()
    os.utime(odd_name, (old, old))
    as_file = a.home / ".restore-staging-20260101-000004-abc127"
    as_file.write_text("a file")
    bk._staging_in_use.add(in_use)
    try:
        assert bk.sweep_stale_staging() == [name]
    finally:
        bk._staging_in_use.discard(in_use)
    assert not stale.exists() and young.exists() and in_use.exists() and odd_name.exists()
    assert link.is_symlink() and (outside / "keep").exists() and as_file.exists()


# -- snapshots -----------------------------------------------------------------------------------------


def test_snapshots_carry_no_key_and_the_old_key_copies_beside_them_are_cleaned(
    tmp_path, monkeypatch
):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    snap = bk.snapshot_db(reason="pre-migration")
    assert snap and not list((a.home / "snapshots").glob("*.secrets.env"))
    side = a.home / "snapshots" / "anthill-20250101-000000-old.db.secrets.env"
    side.write_text(f"ANTHILL_ENCRYPTION_KEY={base64.b64encode(a.key).decode()}\n")
    other = a.home / "snapshots" / "anthill-20250101-000001-old.db.secrets.env"
    other.write_text(f"ANTHILL_ENCRYPTION_KEY={base64.b64encode(os.urandom(32)).decode()}\n")
    keystore.set_backend(keystore.FakeBackend())
    keystore.reset()
    keystore.ensure_ready()  # the move into the Keychain removes the plain copies, snapshots' included
    assert not side.exists() and other.exists()


def test_restoring_a_snapshot_warns_when_this_key_does_not_open_it(tmp_path, monkeypatch):
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    snap = bk.snapshot_db()
    with sqlite3.connect(a.home / "anthill.db") as con:
        con.execute("UPDATE notes SET t = 'changed'")
    ok = bk.restore_snapshot(snap)
    assert ok.warnings == [] and note(a.home) == DB_MARK + "-a" and ok.db_moved_aside.exists()
    with sqlite3.connect(snap) as con:  # a snapshot written under a different key
        con.execute("UPDATE org_settings SET org_model_key_enc = ?", (enc(os.urandom(32)),))
    warned = bk.restore_snapshot(snap)
    assert "db" in warned.restored and "do not open" in warned.warnings[0]
    with pytest.raises(bk.BackupError):
        bk.restore_snapshot(tmp_path / "not-a-database")


# -- the command line -----------------------------------------------------------------------------------


def test_the_command_line_makes_and_restores_a_protected_backup(two, tmp_path, monkeypatch):
    from anthill.cli import app

    _a, b, archive = two
    run = CliRunner().invoke
    pw = tmp_path / "pw"
    pw.write_text(PHRASE + "\n")
    monkeypatch.setenv("ANTHILL_BACKUP_PASSPHRASE_FILE", str(pw))
    made = run(app, ["backup", "-o", str(tmp_path / "cli.anthillbak")])
    assert made.exit_code == 0 and (tmp_path / "cli.anthillbak").read_bytes().startswith(b"ANTHBAK")
    assert PHRASE not in made.output
    pw.write_text("short\n")
    assert run(app, ["backup", "-o", str(tmp_path / "s.anthillbak")]).exit_code == 1
    assert not (tmp_path / "s.anthillbak").exists()
    monkeypatch.setenv("ANTHILL_BACKUP_PASSPHRASE_FILE", str(tmp_path / "missing"))
    assert run(app, ["backup"]).exit_code == 1
    monkeypatch.delenv("ANTHILL_BACKUP_PASSPHRASE_FILE")
    monkeypatch.setenv("ANTHILL_BACKUP_PASSPHRASE", "wrong passphrase here")
    bad = run(app, ["restore", str(archive), "--yes"])
    assert bad.exit_code == 1 and "Wrong passphrase" not in bad.output  # plain words, no traceback
    assert bad.exception is None or isinstance(bad.exception, SystemExit)
    monkeypatch.setenv("ANTHILL_BACKUP_PASSPHRASE", PHRASE)
    good = run(app, ["restore", str(archive), "--yes"])
    assert (
        good.exit_code == 0 and "Restart Anthill" in good.output and note(b.home) == DB_MARK + "-a"
    )
    snap = bk.snapshot_db()
    assert run(app, ["restore-snapshot", str(snap)]).exit_code == 0
    assert run(app, ["restore", str(tmp_path / "nothing.bin"), "--yes"]).exit_code == 1


# -- the page and the routes --------------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    import anthill.web.db as db_mod
    from anthill.web.crypto import make_token

    a = make_install(tmp_path, monkeypatch, "a", keychain=True)
    (a.home / "anthill.db").unlink()
    eng = db_mod.get_engine(a.home / "anthill.db")
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = db_mod.Organization(name="O", slug="o")
    s.add(org)
    s.flush()
    user = db_mod.User(org_id=org.id, email="a@o.com", role="admin", active=True)
    s.add_all([user, db_mod.OrgSettings(org_id=org.id)])
    s.flush()
    s.add(db_mod.InstallSettings(id=1, owner_user_id=user.id))
    s.commit()
    token = make_token(user.id, org.id, "admin")
    s.close()
    client = TestClient(app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    client.cookies.set("session_token", token)
    yield SimpleNamespace(client=client, home=a.home, app=app_mod, key=a.key)
    app_mod._engine.dispose()
    app_mod._engine = app_mod._SessionFactory = None


def events(web) -> list[tuple[str, str]]:
    from anthill.web.db import AuditLog

    s = web.app._SessionFactory()
    rows = [(r.event, r.detail or "") for r in s.query(AuditLog).all()]
    s.close()
    return rows


def test_the_page_states_what_is_and_is_not_protected(web, monkeypatch):
    monkeypatch.setattr(keystore, "_disk_encryption", lambda: "on")
    page = " ".join(web.client.get("/backup").text.split())
    for text in (
        "Choose a passphrase. Without it this backup cannot be restored, and we cannot recover it for you.",
        "the key is carried inside it",
        "Model files are not in the backup",
        "does not protect: the files on this computer while Anthill runs, and any program running as you",
        "Older backups and old copies of <code>secrets.env</code>",
        "still open every saved password",
        "the way back if this Mac's saved key is ever lost",
        "Restart Anthill</b> afterwards",
        'action="/backup/export"',
    ):
        assert text in page, text
    for stale in (
        "includes <code>secrets.env</code>",
        "includes the fine-tuned model",
        "(with model)",
        "?model=",
    ):
        assert stale not in page


def test_exporting_through_the_page_audits_and_streams_a_protected_file(web):
    r = web.client.post("/backup/export", data={"passphrase": PHRASE, "confirm": PHRASE})
    assert (
        r.status_code == 200
        and r.content.startswith(b"ANTHBAK")
        and PHRASE.encode() not in r.content
    )
    assert r.headers["content-disposition"].endswith('.anthillbak"')
    assert any(
        a == "backup.export" and d.startswith("protected ") and PHRASE not in d
        for a, d in events(web)
    )
    for data, where in (
        ({"passphrase": "short", "confirm": "short"}, "error=short"),
        ({"passphrase": PHRASE, "confirm": PHRASE + "x"}, "error=mismatch"),
    ):
        bad = web.client.post("/backup/export", data=data, follow_redirects=False)
        assert bad.status_code == 303 and where in bad.headers["location"]
    assert "at least 12 characters" in web.client.get("/backup?error=short").text
    assert "backup.export_failed" in [a for a, _ in events(web)]


def test_restoring_through_the_page_fails_closed_on_a_wrong_passphrase_and_audits(web):
    data = web.client.post("/backup/export", data={"passphrase": PHRASE, "confirm": PHRASE}).content
    before = tree(web.home)

    def restore(phrase):
        return web.client.post(
            "/backup/restore",
            files={"file": ("b.anthillbak", data, "application/octet-stream")},
            data={"passphrase": phrase},
            follow_redirects=False,
        )

    bad = restore("wrong wrong wrong")
    assert "error=passphrase" in bad.headers["location"] and tree(web.home) == before
    page = web.client.get("/backup?error=passphrase").text
    assert "Wrong passphrase, or the file is damaged - nothing was changed." in page
    assert "empty" in restore("").headers["location"]
    ok = restore(PHRASE)
    assert "restored=1" in ok.headers["location"] and "restart=1" in ok.headers["location"]
    rows = events(web)
    assert any(a == "backup.restore_failed" and d.startswith("failed closed: ") for a, d in rows)
    assert any(a == "backup.restore" and d.startswith("protected ") for a, d in rows)
    assert {a for a, _ in rows} >= {"backup.export", "backup.restore", "backup.restore_failed"}
    assert all("wrong wrong" not in d for _, d in events(web))


def test_an_old_plain_backup_through_the_page_is_audited_as_unprotected(web, tmp_path):
    old = plain_archive(tmp_path / "old.tar.gz", web.home, key=web.key)
    r = web.client.post(
        "/backup/restore",
        files={"file": ("old.tar.gz", old.read_bytes(), "application/gzip")},
        data={"passphrase": PHRASE},
        follow_redirects=False,
    )
    assert "restored=1" in r.headers["location"] and "unprotected=1" in r.headers["location"]
    assert any(a == "backup.restore" and d.startswith("unprotected ") for a, d in events(web))
