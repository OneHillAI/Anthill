"""The key layer (anthill/keystore.py): where the data key and the JWT secret live, and the start-up decision
table. Spec: docs/specs/encryption-at-rest-phase1.md. Model-free; the Keychain is an in-memory FakeBackend
and the real backend only ever runs against a stubbed subprocess. Keys here are test values."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from anthill import keystore
from anthill.keystore import FakeBackend, KeyBackendError, KeyUnavailable
from anthill.web import crypto, key_startup

KEY = base64.b64encode(b"k" * 32).decode()
JWT = "legacy-jwt-secret-for-tests"
OTHER_KEY = base64.b64encode(b"x" * 32).decode()
NOTE_LINE = "OPENAI_API_KEY=sk-not-a-real-key"


class World:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.home = tmp_path / "project" / "data"
        self.home.mkdir(parents=True)
        self.project = self.home.parent
        self.backend = FakeBackend()
        self.has_values = False
        self.opens = True
        monkeypatch.chdir(self.project)
        monkeypatch.setenv("ANTHILL_HOME", str(self.home))
        monkeypatch.setenv("ANTHILL_DB", str(self.home / "anthill.db"))
        for name in ("ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET", "ANTHILL_ALLOW_EPHEMERAL_KEY"):
            monkeypatch.delenv(name, raising=False)
        keystore.set_backend(self.backend)
        keystore.set_data_hooks(lambda: self.has_values, lambda key: self.opens)
        self.monkeypatch = monkeypatch

    @property
    def secrets_env(self) -> Path:
        return self.home / "secrets.env"

    @property
    def dot_env(self) -> Path:
        return self.project / ".env"

    @property
    def marker(self) -> Path:
        return self.home / "key-location.json"

    def legacy(self, *, files: bool = True) -> None:
        self.monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", KEY)
        self.monkeypatch.setenv("ANTHILL_JWT_SECRET", JWT)
        if files:
            lines = f"ANTHILL_JWT_SECRET={JWT}\nANTHILL_ENCRYPTION_KEY={KEY}\n"
            self.secrets_env.write_text(lines + NOTE_LINE + "\n")
            self.dot_env.write_text(f"export ANTHILL_ENCRYPTION_KEY='{KEY}'\n# keep\n")

    def start(self) -> list[str]:
        keystore.reset()
        return keystore.ensure_ready()

    def item(self) -> dict:
        (secret,) = self.backend.items.values()
        return json.loads(base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4)))


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    yield world
    key_startup.register()


# -- a: the marker exists ------------------------------------------------------


def test_fresh_install_makes_one_item_and_a_marker_with_no_key_in_it(w):
    assert w.start() == []
    assert keystore.describe()["where"] == "keychain"
    assert len(w.backend.items) == 1
    item = w.item()
    assert (
        base64.b64decode(item["data_key"]) == keystore.data_key() and len(keystore.data_key()) == 32
    )
    doc = json.loads(w.marker.read_text())
    assert sorted(doc) == ["account", "check", "version", "where"] and doc["where"] == "keychain"
    assert re.fullmatch(r"key:[0-9a-f]{32}", doc["account"]) and doc["account"] in w.backend.items
    assert KEY not in w.marker.read_text() and item["data_key"] not in w.marker.read_text()
    assert doc["check"] == keystore.key_check(keystore.data_key())
    assert (w.marker.stat().st_mode & 0o777) == 0o600
    assert not list(w.home.glob("*.tmp-*")) and not w.secrets_env.exists()


def test_the_next_start_reads_the_same_key_from_the_item(w):
    w.start()
    first, jwt = keystore.data_key(), keystore.jwt_secret()
    w.start()
    assert (keystore.data_key(), keystore.jwt_secret()) == (first, jwt)
    assert len(w.backend.items) == 1


@pytest.mark.parametrize("switch", [False, True])
def test_marker_with_a_missing_item_refuses_and_never_makes_a_key(w, monkeypatch, switch):
    w.start()
    w.backend.items.clear()  # a copied data folder, or the item was removed
    before = w.marker.read_text()
    if switch:
        monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    with pytest.raises(KeyUnavailable, match="not in this Mac's Keychain"):
        w.start()
    with pytest.raises(KeyUnavailable):
        keystore.data_key()
    assert w.backend.items == {} and w.marker.read_text() == before


def test_marker_with_a_different_item_or_a_bad_marker_or_no_keychain_refuses(w):
    w.start()
    account = json.loads(w.marker.read_text())["account"]
    packed = w.backend.items[account]
    w.backend.items[account] = keystore._pack(keystore._Keys(b"z" * 32, "other", "keychain"))
    with pytest.raises(KeyUnavailable, match="not the one"):
        w.start()
    w.backend.items[account] = packed
    w.marker.write_text("{not json")
    with pytest.raises(KeyUnavailable, match="cannot tell where its key is"):
        w.start()
    w.marker.write_text(json.dumps({"version": 1, "where": "keychain", "account": "key:../x"}))
    with pytest.raises(KeyUnavailable):
        w.start()
    w.marker.write_text(
        json.dumps({"version": 1, "where": "keychain", "account": account, "check": "0" * 16})
    )
    w.backend._available = False
    with pytest.raises(KeyUnavailable, match="has none"):
        w.start()


def test_an_unreadable_keychain_refuses_in_plain_words(w, monkeypatch):
    w.start()

    def boom(account):
        raise KeyBackendError("exit 51 secret-looking-detail")

    monkeypatch.setattr(w.backend, "get", boom)
    with pytest.raises(KeyUnavailable) as err:
        w.start()
    assert "secret-looking-detail" not in str(err.value)


def test_a_marker_start_removes_key_lines_that_equal_the_item_and_keeps_the_rest(w):
    w.legacy()
    w.start()  # migration
    # a crash between "marker written" and "lines removed" leaves the lines behind: put them back
    w.secrets_env.write_text(
        f'ANTHILL_ENCRYPTION_KEY={KEY}\nANTHILL_JWT_SECRET="{JWT}"\n{NOTE_LINE}\n'
    )
    w.dot_env.write_text(f'export ANTHILL_ENCRYPTION_KEY="{KEY}"\nOTHER=1\n')
    notes = w.start()
    assert notes == []
    for path, kept in ((w.secrets_env, NOTE_LINE), (w.dot_env, "OTHER=1")):
        text = path.read_text()
        assert KEY not in text and JWT not in text and kept in text
        assert text.count("moved to this Mac's Keychain") == 1
    again = w.secrets_env.read_text()
    w.start()
    assert w.secrets_env.read_text() == again  # idempotent


def test_a_different_or_fancy_key_line_is_left_alone_with_a_note(w):
    w.legacy(files=False)
    w.start()
    w.secrets_env.write_text(f"ANTHILL_ENCRYPTION_KEY={OTHER_KEY}\n{NOTE_LINE}\n")
    w.dot_env.write_text(f"ANTHILL_ENCRYPTION_KEY={KEY} # moved by hand\n")
    notes = w.start()
    assert len(notes) == 2 and all("left alone" in n for n in notes)
    assert OTHER_KEY in w.secrets_env.read_text() and KEY in w.dot_env.read_text()


def test_a_linked_env_file_is_never_written_through(w, tmp_path):
    w.legacy(files=False)
    w.start()
    target = tmp_path / "elsewhere.env"
    target.write_text(f"ANTHILL_ENCRYPTION_KEY={KEY}\n")
    w.secrets_env.symlink_to(target)
    notes = w.start()
    assert KEY in target.read_text() and any("is a link" in n for n in notes)


# -- b: one-step migration --------------------------------------------------------


def test_legacy_key_is_moved_in_one_step_with_the_same_values(w):
    w.legacy()
    w.has_values = True
    assert w.start() == []
    assert keystore.data_key() == base64.b64decode(KEY) and keystore.jwt_secret() == JWT
    assert w.item() == {"v": 1, "data_key": KEY, "jwt_secret": JWT}
    assert KEY not in w.secrets_env.read_text() and JWT not in w.secrets_env.read_text()
    assert NOTE_LINE in w.secrets_env.read_text() and KEY not in w.dot_env.read_text()
    assert "# keep" in w.dot_env.read_text()
    assert "ANTHILL_ENCRYPTION_KEY" not in os.environ  # the process no longer carries a plain copy
    assert json.loads(w.marker.read_text())["check"] == keystore.key_check(base64.b64decode(KEY))


def test_ciphertext_made_before_the_move_still_opens_after_it(w):
    w.legacy()
    token = crypto.encrypt("saved-password")
    w.start()
    assert crypto.decrypt(token) == "saved-password"
    assert base64.b64decode(token)[12:]  # format unchanged: base64(nonce + ct + tag)


@pytest.mark.parametrize("failing", ["add", "readback", "marker", "verify"])
def test_a_failed_step_changes_nothing_and_the_app_starts_on_the_legacy_key(
    w, monkeypatch, failing
):
    w.legacy()
    files = (w.secrets_env.read_text(), w.dot_env.read_text())
    if failing == "add":
        monkeypatch.setattr(
            w.backend, "set", lambda a, s: (_ for _ in ()).throw(KeyBackendError("x"))
        )
    elif failing == "readback":
        monkeypatch.setattr(w.backend, "get", lambda a: "different")
    elif failing == "marker":
        monkeypatch.setattr(
            keystore, "_write_marker", lambda *a: (_ for _ in ()).throw(OSError("disk"))
        )
    else:
        monkeypatch.setattr(
            keystore, "_open_item", lambda m: (_ for _ in ()).throw(KeyUnavailable("no"))
        )
    notes = w.start()
    assert len(notes) == 1 and "could not move its key" in notes[0]
    assert (
        keystore.data_key() == base64.b64decode(KEY)
        and keystore.describe()["where"] == "plain file"
    )
    assert (w.secrets_env.read_text(), w.dot_env.read_text()) == files
    assert not w.marker.exists() and w.backend.items == {} and not list(w.home.glob("*.tmp-*"))
    assert os.environ["ANTHILL_ENCRYPTION_KEY"] == KEY


def test_a_failed_step_after_a_marker_deletes_only_what_it_made(w, monkeypatch):
    w.legacy()
    w.backend.items["key:" + "a" * 32] = "someone-elses-item"  # must survive
    calls = {"n": 0}  # the first call (start of ensure_ready) sees no marker; later calls fail

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        raise OSError("late")

    monkeypatch.setattr(keystore, "_read_marker", flaky)
    notes = w.start()
    assert "could not move" in notes[0] and not w.marker.exists()
    assert list(w.backend.items) == ["key:" + "a" * 32]


def test_values_that_the_legacy_key_cannot_open_stop_the_move(w):
    w.legacy()
    w.has_values, w.opens = True, False
    notes = w.start()
    assert "opens none of the stored passwords" in notes[0]
    assert (
        not w.marker.exists()
        and w.backend.items == {}
        and keystore.data_key() == base64.b64decode(KEY)
    )


def test_an_unreadable_database_stops_the_move_without_calling_it_empty(w):
    w.legacy()

    def locked():
        raise keystore.ValuesUnreadable("locked")

    keystore.set_data_hooks(locked, lambda k: True)
    notes = w.start()
    assert "could not move" in notes[0] and not w.marker.exists()


def test_a_legacy_key_that_is_not_32_bytes_is_refused(w, monkeypatch):
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(KeyUnavailable, match="32 bytes"):
        w.start()
    assert w.backend.items == {}


# -- c: no marker, no legacy key ----------------------------------------------------


@pytest.mark.parametrize("switch", [False, True])
def test_stored_values_and_no_key_refuse_and_make_nothing(w, monkeypatch, switch):
    w.has_values = True
    if switch:
        monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    with pytest.raises(KeyUnavailable, match="will not make a new key"):
        w.start()
    assert w.backend.items == {} and not w.marker.exists()


def test_a_fresh_install_that_cannot_store_its_key_refuses_and_leaves_nothing(w, monkeypatch):
    monkeypatch.setattr(
        keystore, "_write_marker", lambda *a: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(KeyUnavailable, match="could not store its new key"):
        w.start()
    assert w.backend.items == {} and not w.marker.exists()


# -- d / e: no Keychain, and the dev switch -------------------------------------------


def test_without_a_keychain_the_key_stays_where_it_is(w):
    w.backend._available = False
    w.legacy()
    files = (w.secrets_env.read_text(), w.dot_env.read_text())
    assert w.start() == []
    assert keystore.data_key() == base64.b64decode(KEY)
    d = keystore.describe()
    assert (
        d["where"] == "plain file"
        and d["protected"] is False
        and "not protected" in d["plain_words"]
    )
    assert (w.secrets_env.read_text(), w.dot_env.read_text()) == files and not w.marker.exists()


def test_without_a_keychain_and_without_a_key_values_refuse_and_none_needs_the_switch(
    w, monkeypatch
):
    w.backend._available = False
    w.has_values = True
    with pytest.raises(KeyUnavailable, match="saved passwords but no encryption key"):
        w.start()
    w.has_values = False
    with pytest.raises(KeyUnavailable, match="no encryption key"):
        w.start()
    monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    w.start()
    assert keystore.describe()["where"] == "temporary" and len(keystore.data_key()) == 32


def test_the_dev_switch_gives_a_throwaway_key_only_with_no_values_and_no_marker(w, monkeypatch):
    monkeypatch.setenv("ANTHILL_ALLOW_EPHEMERAL_KEY", "1")
    w.start()
    assert keystore.describe()["where"] == "temporary" and w.backend.items == {}
    w.has_values = True
    with pytest.raises(KeyUnavailable):
        w.start()


# -- the real backend, against a stubbed subprocess -------------------------------------


class Recorder:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, args, input=None, **kw):
        self.calls.append((args, input))
        assert kw["timeout"] > 0 and kw["capture_output"]
        return subprocess.CompletedProcess(args, *self.result)


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(keystore, "_SECURITY", "/usr/bin/security")
    rec = Recorder()
    monkeypatch.setattr(keystore.subprocess, "run", rec)
    return rec


def test_adding_an_item_sends_the_secret_on_stdin_never_in_argv_and_never_updates(mac):
    keystore.MacSecurityBackend().set("key:" + "a" * 32, "SECRETvalue-_.")
    ((args, stdin),) = mac.calls
    assert args == ["/usr/bin/security", "-i"]
    command = (stdin or b"").decode()
    assert command.startswith("add-generic-password -a key:") and "SECRETvalue-_." in command
    assert " -U" not in command and "SECRETvalue" not in " ".join(args)
    assert f"-s {keystore.SERVICE}" in command


def test_an_item_that_exists_is_an_error_not_an_overwrite(mac):
    mac.result = (0, b"", b"security: SecKeychainItemCreateFromContent: already exists\n")
    with pytest.raises(KeyBackendError, match="did not accept"):
        keystore.MacSecurityBackend().set("key:" + "a" * 32, "abc")
    mac.result = (45, b"", b"")
    with pytest.raises(KeyBackendError):
        keystore.MacSecurityBackend().set("key:" + "a" * 32, "abc")


def test_reading_and_deleting_use_the_find_and_delete_commands(mac):
    be = keystore.MacSecurityBackend()
    mac.result = (0, b"the-secret\n", b"")
    assert be.get("key:" + "a" * 32) == "the-secret"
    mac.result = (44, b"", b"")
    assert be.get("key:" + "a" * 32) is None
    mac.result = (51, b"", b"")
    with pytest.raises(KeyBackendError):
        be.get("key:" + "a" * 32)
    mac.result = (0, b"", b"")
    be.delete("key:" + "a" * 32)
    assert mac.calls[-1][0][:2] == ["/usr/bin/security", "delete-generic-password"]
    assert mac.calls[0][0][1] == "find-generic-password" and "-w" in mac.calls[0][0]


@pytest.mark.parametrize("bad", ["", "a b", "a\nadd-generic-password -a x", 'a"b', "a;b", "a$b"])
def test_only_plain_tokens_reach_the_tool(mac, bad):
    be = keystore.MacSecurityBackend()
    with pytest.raises(KeyBackendError):
        be.set("key:" + "a" * 32, bad)
    with pytest.raises(KeyBackendError):
        be.get(bad)
    assert mac.calls == []


def test_a_tool_that_does_not_answer_is_a_backend_error(monkeypatch):
    monkeypatch.setattr(keystore, "_SECURITY", "/usr/bin/security")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("security", 15)

    monkeypatch.setattr(keystore.subprocess, "run", hang)
    with pytest.raises(KeyBackendError, match="did not answer"):
        keystore.MacSecurityBackend().get("key:" + "a" * 32)


def test_the_test_guard_means_the_real_backend_is_never_available_in_tests():
    assert keystore.MacSecurityBackend().available() is False


# -- adopt (bring-in) ---------------------------------------------------------------


def test_adopt_moves_to_a_new_item_and_keeps_the_old_until_finished(w):
    w.start()
    old = json.loads(w.marker.read_text())["account"]
    new_key = b"n" * 32
    finish, _undo = keystore.adopt(new_key, "new-jwt")
    new = json.loads(w.marker.read_text())["account"]
    assert new != old and set(w.backend.items) == {old, new}
    assert keystore.data_key() == new_key and keystore.jwt_secret() == "new-jwt"
    w.start()  # a restart now reads the new item
    assert keystore.data_key() == new_key
    finish()
    assert set(w.backend.items) == {new}


def test_a_failed_bring_in_puts_the_old_marker_back_and_removes_the_new_item(w):
    w.start()
    old_key, before = keystore.data_key(), w.marker.read_text()
    _finish, undo = keystore.adopt(b"n" * 32, "new-jwt")
    undo()
    assert w.marker.read_text() == before and len(w.backend.items) == 1
    assert keystore.data_key() == old_key  # re-read from the old item


def test_adopt_validates_before_changing_anything(w):
    w.start()
    before = (w.marker.read_text(), dict(w.backend.items))
    for key, jwt in ((b"short", "j"), (b"n" * 32, "")):
        with pytest.raises(keystore.KeystoreError):
            keystore.adopt(key, jwt)
    assert (w.marker.read_text(), w.backend.items) == before


def test_adopt_that_fails_midway_leaves_the_old_marker_and_no_new_item(w, monkeypatch):
    w.start()
    before = (w.marker.read_text(), dict(w.backend.items))
    monkeypatch.setattr(
        keystore, "_write_marker", lambda *a: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        keystore.adopt(b"n" * 32, "new-jwt")
    assert (w.marker.read_text(), w.backend.items) == before


# -- what the interface may say ---------------------------------------------------------


def test_describe_says_where_the_key_is_without_any_secret(w, monkeypatch):
    w.start()
    d = keystore.describe()
    assert d["where"] == "keychain" and d["protected"] is True
    assert d["plain_words"] == "The key is stored in this Mac's Keychain, not in the data folder."
    assert keystore.data_key().hex() not in json.dumps(d) and set(d) == {
        "where",
        "protected",
        "plain_words",
        "disk_encryption",
    }


@pytest.mark.parametrize(
    ("stdout", "platform", "expected"),
    [
        (b"FileVault is On.\n", "darwin", "on"),
        (b"FileVault is Off.\n", "darwin", "off"),
        (b"Encryption in progress\n", "darwin", "unknown"),
        (b"", "darwin", "unknown"),
        (b"FileVault is On.\n", "linux", "unknown"),
    ],
)
def test_disk_encryption_comes_from_a_stubbed_fdesetup(monkeypatch, stdout, platform, expected):
    rec = Recorder(0, stdout)
    monkeypatch.setattr(keystore.subprocess, "run", rec)
    monkeypatch.setattr(keystore, "sys", SimpleNamespace(platform=platform))
    assert keystore.describe()["disk_encryption"] == expected
    if platform == "darwin":
        assert rec.calls[0][0] == ["/usr/bin/fdesetup", "status"]


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("fdesetup", 3), OSError("gone"), ValueError("x")]
)
def test_describe_never_raises_when_fdesetup_fails(monkeypatch, error):
    def boom(*a, **k):
        raise error

    monkeypatch.setattr(keystore.subprocess, "run", boom)
    monkeypatch.setattr(keystore, "sys", SimpleNamespace(platform="darwin"))
    assert keystore.describe()["disk_encryption"] == "unknown"


# -- signing keys and crypto ---------------------------------------------------------------


def test_signing_keys_differ_by_purpose_and_come_from_the_jwt_secret(w):
    w.legacy(files=False)
    w.start()
    a, b = keystore.signing_key("slack-oauth"), keystore.signing_key("task-result-review")
    assert len(a) == 32 and a != b and a == keystore.signing_key("slack-oauth")
    assert JWT.encode() not in (a, b)


def test_no_fixed_fallback_secret_is_left_in_the_code():
    root = Path(__file__).resolve().parents[1] / "anthill"
    hits = [str(p) for p in root.rglob("*.py") if "anthill-dev" in p.read_text(errors="replace")]
    assert hits == []


def test_crypto_takes_its_key_from_the_key_layer_and_refuses_without_one(w, monkeypatch):
    w.backend._available = False
    with pytest.raises(KeyUnavailable):
        crypto.encrypt("x")
    with pytest.raises(KeyUnavailable):
        crypto.make_token(1, 1, "admin")
    w.legacy(files=False)
    keystore.reset()
    token = crypto.encrypt("hello")
    assert crypto.decrypt(token) == "hello"
    assert crypto.decrypt_with_key(base64.b64decode(KEY), token) == "hello"
    monkeypatch.setattr(crypto, "_KEY", b"o" * 32)  # the override hook still wins
    with pytest.raises(Exception):  # noqa: B017 - InvalidTag
        crypto.decrypt(token)
