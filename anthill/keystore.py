"""The one owner of the data key and the JWT secret (spec: docs/specs/encryption-at-rest-phase1.md).

On a Mac both secrets live in ONE login-Keychain item (a small JSON blob). The data folder holds only a
non-secret marker, ``key-location.json``: where the key is, the item's account name and a short check value.
The marker tells a missing item (refuse, never a new key) from a fresh install. Elsewhere the key stays where
it always was (environment, ``secrets.env``, ``.env``). ``ensure_ready()`` is the whole start-up decision
table. Stored values are encrypted exactly as before with the same key value; only its home moves.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger("anthill.keystore")

SERVICE = "org.onehill.anthill"
MARKER_FILE = "key-location.json"
ENV_DATA_KEY = "ANTHILL_ENCRYPTION_KEY"
ENV_JWT_SECRET = "ANTHILL_JWT_SECRET"
ENV_ALLOW_EPHEMERAL = "ANTHILL_ALLOW_EPHEMERAL_KEY"

_SECURITY = "/usr/bin/security"
_FDESETUP = "/usr/bin/fdesetup"
_PLAIN = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_:.")
_ACCOUNT = re.compile(r"key:[0-9a-f]{32}")
_MOVED = "# The encryption key and the session secret moved to this Mac's Keychain."


class KeystoreError(Exception):
    """A key-layer step failed. The message is plain and never carries a secret."""


class KeyUnavailable(KeystoreError):
    """The key cannot be produced; Anthill must not start (and never makes a new key over stored data)."""


class KeyBackendError(KeystoreError):
    """The Keychain did not answer as expected."""


class ValuesUnreadable(KeystoreError):
    """The database exists but could not be read, so "are there stored values" is unknown. Not "no"."""


# -- backends ----------------------------------------------------------------


class FakeBackend:
    """In-memory Keychain for tests. Like the real one, adding an item that exists is an error."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.items: dict[str, str] = {}

    def available(self) -> bool:
        return self._available

    def get(self, account: str) -> str | None:
        return self.items.get(account)

    def set(self, account: str, secret: str) -> None:
        if account in self.items:
            raise KeyBackendError("the Keychain item already exists; nothing was changed")
        self.items[account] = secret

    def delete(self, account: str) -> None:
        self.items.pop(account, None)


def _check_plain(value: str) -> None:
    if not value or not set(value) <= _PLAIN:
        raise KeyBackendError("a Keychain account or secret was not a plain token")


def _security(args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [_SECURITY, *args], input=stdin, capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        raise KeyBackendError("the macOS Keychain tool did not answer") from None


class MacSecurityBackend:
    """The macOS login Keychain through /usr/bin/security. The secret is sent as a command on stdin
    (``security -i``), so it is never in argv. Items are only ever added (no ``-U``): an existing item
    is an error, never overwritten. Any program of the same user can read the item while the Keychain is
    unlocked; the spec lists that as a limit of phase 1."""

    def available(self) -> bool:
        return sys.platform == "darwin" and os.path.exists(_SECURITY)

    def get(self, account: str) -> str | None:
        _check_plain(account)
        proc = _security(["find-generic-password", "-a", account, "-s", SERVICE, "-w"])
        if proc.returncode == 44:  # item not found
            return None
        if proc.returncode != 0:
            raise KeyBackendError(f"the Keychain could not be read (exit {proc.returncode})")
        return proc.stdout.decode("utf-8", "replace").strip() or None

    def set(self, account: str, secret: str) -> None:
        _check_plain(account)
        _check_plain(secret)
        line = f"add-generic-password -a {account} -s {SERVICE} -w {secret}\n"
        proc = _security(["-i"], stdin=line.encode())
        shown = (proc.stdout + proc.stderr).decode("utf-8", "replace").replace("security>", "")
        if proc.returncode != 0 or shown.strip():
            raise KeyBackendError("the Keychain did not accept the key; nothing was stored")

    def delete(self, account: str) -> None:
        _check_plain(account)
        proc = _security(["delete-generic-password", "-a", account, "-s", SERVICE])
        if proc.returncode not in (0, 44):
            raise KeyBackendError(
                f"the Keychain item could not be removed (exit {proc.returncode})"
            )


# -- state -------------------------------------------------------------------


@dataclass(frozen=True)
class _Keys:
    data_key: bytes
    jwt: str
    where: str  # "keychain" | "plain file" | "temporary"


_lock = threading.RLock()
_backend: FakeBackend | MacSecurityBackend | None = None
_probe: Callable[[], bool] | None = (
    None  # any stored encrypted value? (registered by the web layer)
)
_checker: Callable[[bytes], bool] | None = None  # does this key open a stored value?
_keys: _Keys | None = None
_ready = False


def set_backend(backend: FakeBackend | MacSecurityBackend | None) -> None:
    global _backend
    _backend = backend
    reset()


def set_data_hooks(probe: Callable[[], bool], checker: Callable[[bytes], bool]) -> None:
    global _probe, _checker
    _probe, _checker = probe, checker


def reset() -> None:
    """Forget what was resolved (tests, and after the key location changed)."""
    global _keys, _ready
    with _lock:
        _keys, _ready = None, False


def _be() -> FakeBackend | MacSecurityBackend:
    return _backend if _backend is not None else MacSecurityBackend()


def protected_store_available() -> bool:
    return _be().available()


def values_exist() -> bool:
    return bool(_probe()) if _probe else False  # a probe that cannot read raises ValuesUnreadable


def _ephemeral_allowed() -> bool:
    return os.environ.get(ENV_ALLOW_EPHEMERAL, "").strip().lower() in ("1", "true", "yes")


# -- the item and the marker -------------------------------------------------


def _pack(keys: _Keys) -> str:
    blob = {"v": 1, "data_key": base64.b64encode(keys.data_key).decode(), "jwt_secret": keys.jwt}
    return base64.urlsafe_b64encode(json.dumps(blob).encode()).rstrip(b"=").decode()


def _unpack(secret: str) -> _Keys:
    try:
        blob = json.loads(base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4)))
        key = base64.b64decode(blob["data_key"], validate=True)
        jwt = blob["jwt_secret"]
        if len(key) == 32 and isinstance(jwt, str) and jwt:
            return _Keys(key, jwt, "keychain")
    except (ValueError, KeyError, TypeError, binascii.Error):
        pass
    raise KeyUnavailable("The key in the Keychain item could not be read.")


def key_check(data_key: bytes) -> str:
    """A short value that proves which key an item holds. Not usable to find the key."""
    return hmac.new(data_key, b"anthill key check", hashlib.sha256).digest()[:8].hex()


def marker_path() -> Path:
    from .backup import data_paths

    return data_paths()["home"] / MARKER_FILE


def marker_exists() -> bool:
    return marker_path().exists()


def _read_marker() -> dict | None:
    path = marker_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if (
            doc["where"] == "keychain"
            and _ACCOUNT.fullmatch(doc["account"])
            and len(doc["check"]) == 16
        ):
            return doc
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise KeyUnavailable(f"{MARKER_FILE} cannot be read, so Anthill cannot tell where its key is.")


def _write_atomic(path: Path, text: str) -> None:
    """Temp file (0600), fsync, rename over ``path``, fsync the folder."""
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _write_marker(account: str, keys: _Keys) -> None:
    doc = {"version": 1, "where": "keychain", "account": account, "check": key_check(keys.data_key)}
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(doc, indent=2) + "\n")


def _open_item(marker: dict) -> _Keys:
    """The keys the marker points at. A missing, unreadable or different item is a refusal."""
    be = _be()
    if not be.available():
        raise KeyUnavailable(
            "This data folder keeps its key in a macOS Keychain and this computer has none. Open it on "
            "the Mac it came from, or restore a backup that holds its key."
        )
    try:
        raw = be.get(marker["account"])
    except KeystoreError:
        raise KeyUnavailable(
            "Anthill could not read its key from the Keychain (is it locked?)."
        ) from None
    if raw is None:
        raise KeyUnavailable(
            "Anthill's key is not in this Mac's Keychain, but this data folder says it should be (it was "
            "copied from another Mac, or the item was removed). Restore the item or a backup that holds it."
        )
    keys = _unpack(raw)
    if not hmac.compare_digest(key_check(keys.data_key), marker["check"]):
        raise KeyUnavailable(
            "The key in the Keychain is not the one this data folder was made with."
        )
    return keys


def _install(keys: _Keys) -> str:
    """Add a NEW item under a new account, read it back, write the marker, read both again. On any failure
    delete only what this call made and put the marker back as it was. Returns the account."""
    be, account, packed = _be(), "key:" + secrets.token_hex(16), _pack(keys)
    path = marker_path()
    before = path.read_text(encoding="utf-8") if path.exists() else None
    added = False
    try:
        be.set(account, packed)
        added = True
        if be.get(account) != packed:
            raise KeyBackendError("the Keychain returned a different value than was stored")
        _write_marker(account, keys)
        marker = _read_marker()
        assert marker is not None
        if marker["account"] != account or _open_item(marker).data_key != keys.data_key:
            raise KeyBackendError("the key could not be read back")
    except BaseException:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            _write_atomic(path, before)
        if added:
            try:
                be.delete(account)
            except KeystoreError:
                pass
        raise
    return account


# -- reading the keys ---------------------------------------------------------


def _legacy() -> _Keys | None:
    """The key from the environment (which carries secrets.env and .env), validated."""
    raw = os.environ.get(ENV_DATA_KEY, "")
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise KeyUnavailable(f"{ENV_DATA_KEY} must be 32 bytes, base64-encoded.")
    return _Keys(key, os.environ.get(ENV_JWT_SECRET) or secrets.token_hex(32), "plain file")


_throwaway: list[_Keys] = []  # one throwaway pair per process (the dev and test switch)


def _refuse_no_key(why: str) -> KeyUnavailable:
    return KeyUnavailable(
        f"{why} Anthill will not make a new key over data that may already be encrypted. Restore the "
        "key (or a backup of this install that holds it), then open Anthill again."
    )


def _resolve() -> _Keys:
    """Keys for this run without changing anything (the lazy path for code that never ran ensure_ready)."""
    marker = _read_marker()
    if marker:
        return _open_item(marker)  # never a new key, not even with the dev switch
    legacy = _legacy()
    if legacy:
        return legacy
    if values_exist():
        raise _refuse_no_key("This install holds saved passwords but no encryption key.")
    if _ephemeral_allowed():
        if not _throwaway:
            _throwaway.append(_Keys(secrets.token_bytes(32), secrets.token_hex(32), "temporary"))
        return _throwaway[0]
    raise KeyUnavailable(
        f"Anthill has no encryption key. Set one up, or (development only) set {ENV_ALLOW_EPHEMERAL}=1."
    )


def _current() -> _Keys:
    global _keys
    with _lock:
        if _keys is None:
            _keys = _resolve()
        return _keys


def data_key() -> bytes:
    """The one place that hands out the data key."""
    return _current().data_key


def jwt_secret() -> str:
    return _current().jwt


def signing_key(purpose: str) -> bytes:
    """A key for one signing purpose, derived from the JWT secret, so no two uses share a key."""
    info = b"anthill/signing/v1/" + purpose.encode()
    return HKDF(hashes.SHA256(), 32, None, info).derive(jwt_secret().encode())


# -- start-up -----------------------------------------------------------------


def ensure_ready() -> list[str]:
    """The start-up decision table. Resolves the keys (moving or making them when that is safe), and returns
    plain notes to show. Raises KeystoreError (a refusal) when the key is missing or cannot be trusted."""
    global _keys, _ready
    with _lock:
        if _ready:
            return []
        notes: list[str] = []
        marker = _read_marker()
        if marker:
            _keys = _open_item(marker)
            notes += _clean_env_files(_keys)
        elif (legacy := _legacy()) is not None:
            _keys = _migrate(legacy, notes) if protected_store_available() else legacy
        elif protected_store_available() and not _ephemeral_allowed():
            if values_exist():
                raise _refuse_no_key("This install holds saved passwords but no encryption key.")
            _keys = _fresh_install()
        else:
            _keys = _resolve()
        _ready = True
        return notes


def _fresh_install() -> _Keys:
    keys = _Keys(
        secrets.token_bytes(32),
        os.environ.get(ENV_JWT_SECRET) or secrets.token_urlsafe(48),
        "keychain",
    )
    try:
        _install(keys)
    except (KeystoreError, OSError) as exc:
        log.warning("key layer: could not create a key in the Keychain (%s)", type(exc).__name__)
        raise KeyUnavailable(
            "Anthill could not store its new key in the Keychain, so it did not start. Nothing was "
            "created. Unlock the Keychain and open Anthill again."
        ) from None
    log.info("key layer: created a key in the Keychain (fresh install)")
    return keys


def _migrate(legacy: _Keys, notes: list[str]) -> _Keys:
    """One step: the legacy key goes into the Keychain, the marker is written, and only then are the plain
    copies removed. Any failed step leaves the old key where it was; the app starts on it."""
    keys = _Keys(legacy.data_key, legacy.jwt, "keychain")
    try:
        if values_exist() and not (_checker and _checker(legacy.data_key)):
            log.warning("key layer: the saved key opens none of the stored values; not moved")
            notes.append(
                "The saved key opens none of the stored passwords, so it was not moved to the "
                "Keychain. It stays where it was."
            )
            return legacy
        _install(keys)
    except (KeystoreError, OSError) as exc:
        log.warning("key layer: moving the key failed (%s)", type(exc).__name__)
        notes.append(
            "Anthill could not move its key into the Keychain this time. The key is still where it "
            "was and nothing was changed."
        )
        return legacy
    log.info("key layer: moved the key into the Keychain")
    notes += _clean_env_files(keys)
    for name in (ENV_DATA_KEY, ENV_JWT_SECRET):
        os.environ.pop(name, None)
    return keys


# -- removing the plain copies --------------------------------------------------


def _env_files() -> list[Path]:
    """secrets.env in the data home, the source install's .env (beside a ``data`` folder, or here), and the
    ``*.secrets.env`` copies an older version left beside each snapshot."""
    home = marker_path().parent.resolve()
    found = [
        home / "secrets.env",
        *([home.parent / ".env"] if home.name == "data" else []),
        Path.cwd() / ".env",
        *sorted((home / "snapshots").glob("*.secrets.env")),
    ]
    return list(dict.fromkeys(found))


def _key_line(line: str) -> tuple[str, str | None] | None:
    """(name, value) of a plain key line (``export`` and one pair of quotes allowed). A fancier key line gives
    (name, None); any other line gives None."""
    name, sep, value = line.strip().removeprefix("export ").lstrip().partition("=")
    value = value.strip()
    if not sep or name.strip() not in (ENV_DATA_KEY, ENV_JWT_SECRET) or not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (name.strip(), None if re.search(r"[\s#'\"]", value) or not value else value)


def _clean_env_file(path: Path, same: dict[str, str]) -> list[str]:
    """Take one env file's key lines that equal the Keychain's values out. Returns notes."""
    notes: list[str] = []
    out, removed = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        found = _key_line(line)
        if found and found[1] == same[found[0]]:
            removed = True
            continue
        if found:
            notes.append(
                f"A key line in {path.name} was left alone: it is not the key now in the Keychain."
            )
        out.append(line)
    if not removed:
        return notes
    if path.is_symlink():
        notes.append(f"{path.name} is a link, so the key line in it was left for you to remove.")
    elif path.name.endswith(".secrets.env") and not any(
        ln.strip() and not ln.strip().startswith("#") for ln in out
    ):
        path.unlink()  # a snapshot's key copy held nothing else
    else:
        if _MOVED not in out:
            out.insert(0, _MOVED)
        _write_atomic(path, "\n".join(out) + "\n")
    return notes


def _clean_env_files(keys: _Keys) -> list[str]:
    """Remove key lines holding the Keychain's own values from secrets.env and .env (idempotent). Every other
    line stays, with one comment saying the keys moved. A different or fancier key line is left, with a note."""
    same = {ENV_DATA_KEY: base64.b64encode(keys.data_key).decode(), ENV_JWT_SECRET: keys.jwt}
    notes: list[str] = []
    for path in _env_files():
        try:
            if path.is_file():
                notes += _clean_env_file(path, same)
        except (OSError, UnicodeDecodeError):
            notes.append(f"Anthill could not clean the old key lines out of {path.name}.")
    return notes


# -- bring-in: swap the key for the one that came with an earlier copy -----------


def adopt(data_key: bytes, jwt_secret_value: str) -> tuple[Callable[[], None], Callable[[], None]]:
    """Make this key pair the install's (bring-in): a NEW item under a new account and the marker pointing at
    it, validated first. Returns (finish, undo): ``finish`` removes the old item once the bring-in worked,
    ``undo`` puts the old marker back and removes the new item. A failure here changes nothing."""
    if len(data_key) != 32 or not jwt_secret_value:
        raise KeystoreError("The imported key is not a 32-byte key and a session secret.")
    global _keys, _ready
    path, old = marker_path(), _read_marker()
    old_text = path.read_text(encoding="utf-8") if old else None
    keys = _Keys(data_key, jwt_secret_value, "keychain")
    new = _install(keys)
    with _lock:
        _keys, _ready = keys, True

    def finish() -> None:
        if old:
            try:
                _be().delete(old["account"])
            except KeystoreError:
                log.warning("key layer: the previous Keychain item could not be removed")

    def undo() -> None:
        if old_text is None:
            path.unlink(missing_ok=True)
        else:
            _write_atomic(path, old_text)
        try:
            _be().delete(new)
        except KeystoreError:
            pass
        reset()

    return finish, undo


# -- what the interface may say --------------------------------------------------

_WORDS = {
    "keychain": "The key is stored in this Mac's Keychain, not in the data folder.",
    "plain file": (
        "The key is stored in a file beside your data. It is not protected: anyone with a copy of "
        "your data folder can read your saved passwords."
    ),
    "temporary": "The key is temporary and is lost when Anthill stops. Saved passwords will not survive.",
}


def _disk_encryption() -> str:
    if sys.platform != "darwin":
        return "unknown"
    try:
        out = subprocess.run(
            [_FDESETUP, "status"], capture_output=True, timeout=3, check=False
        ).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - describe() never raises: an odd answer from the tool is "unknown"
        return "unknown"
    if "FileVault is On" in out:
        return "on"
    return "off" if "FileVault is Off" in out else "unknown"


def describe() -> dict:
    """Non-secret facts for the interface. Never raises."""
    try:
        if _keys:
            where = _keys.where
        elif marker_exists():
            where = "keychain"
        else:
            where = "plain file" if os.environ.get(ENV_DATA_KEY) else "temporary"
    except Exception:  # noqa: BLE001 - describe() runs on a page and must never raise
        where = "plain file"
    return {
        "where": where,
        "protected": where == "keychain",
        "plain_words": _WORDS[where],
        "disk_encryption": _disk_encryption(),
    }
