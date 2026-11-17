"""Local backup + restore for an Anthill install.

Everything that matters lives *outside* the code, so a code update never touches it. This module is the safety
net against disk loss, a bad migration, or a move to a new machine.

A backup is ONE passphrase-protected ``.anthillbak`` file (see ``backup_crypto``): the whole archive is
encrypted, so the file shows nothing without the passphrase. It holds
  - the SQLite DB (settings, memory, gold training examples, cache, snippets, teams, MCP, audit)
  - the wikis (legacy workspace, personal and project wikis, the org wiki with its ``raw/`` uploads), files, skills
  - the data key and the session secret, as ``data/key-export.json`` INSIDE the encrypted stream: without the key
    every encrypted field (cloud / AWS / Modal / MCP secrets) is unreadable on restore. This is the recovery route
    when the saved key is lost. The file ``secrets.env`` and ``key-location.json`` are never written to a backup;
    the other lines of ``secrets.env`` (optional API keys) ride along inside that same export.
Model files (Ollama blobs) are not included; they hold no user data and are downloaded again.

Public API:
  create_backup()    -> write one protected archive of the whole data dir
  restore_backup()   -> reload such an archive, or an older plain ``.tar.gz``; the whole archive is verified in a
                        staging folder, and its key must open every stored value, before any current data is touched
  snapshot_db()      -> a fast pre-migration copy of the DB (keeps the last N); carries no key
  restore_snapshot() -> put one of those copies back
  sweep_stale_staging() -> remove staging folders a crashed restore left behind
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from . import backup_crypto, keystore, stored_values
from .wiki.workspace import org_wikis_base

BACKUP_FORMAT = 2
MIN_PASSPHRASE = 12
ENV_BACKUP_PASSPHRASE = "ANTHILL_BACKUP_PASSPHRASE"
ENV_BACKUP_PASSPHRASE_FILE = "ANTHILL_BACKUP_PASSPHRASE_FILE"
_SMALL_MAX = 1 << 20
_KEY_ENV = ("ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_ENV_FILE_MAX = 64 * 1024
_MAX_MEMBERS = 500_000  # entries read from one archive; more is a bomb, not a backup
_FREE_SPACE_SHARE = (
    0.9  # an archive may fill at most this much of the free space on the staging volume
)
_COPY_CHUNK = 1 << 20
_SPECIAL = ("manifest.json", "data/key-export.json", "data/secrets.env")
_STAGING = re.compile(r"\.restore-staging-\d{8}-\d{6}-[0-9a-f]{6}")
_STAGING_MIN_AGE = 3600  # seconds: a folder this young may belong to a restore in another process
_staging_in_use: set[Path] = set()

# Directory trees archived under ``data/``: (key in data_paths(), archive name). Backup and restore both
# walk this one list, so a tree can never be saved without being restored (or the reverse).
_TREES = (
    ("workspace", "data/workspace"),
    ("wikis", "data/wikis"),
    ("org_wiki", "data/org-wiki"),
    ("org_wikis", "data/org-wikis"),
    ("files", "data/files"),
    ("skills", "data/skills"),
)

BackupError = (
    backup_crypto.BackupCryptoError
)  # a backup or restore did not go through; plain words, no secret


class PassphraseNeeded(BackupError):
    """No passphrase was given where one is required."""


class PassphraseTooShort(BackupError):
    """A new backup needs a longer passphrase."""


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def data_paths() -> dict[str, Path]:
    """Resolve where this install keeps its state, honouring the same env vars the app sets.

    Works for both the installed app (``~/Library/Application Support/Anthill``) and a dev/source run
    (``<clone>/data`` + ``<clone>/workspace``)."""
    # Fallbacks mirror each component's own default exactly, so this resolves correctly whether the
    # env vars are set (installed app: all under ANTHILL_HOME) or not (dev: cwd-relative defaults).
    db = Path(os.environ.get("ANTHILL_DB", "data/anthill.db"))
    home = Path(os.environ.get("ANTHILL_HOME", str(db.parent)))
    return {
        "home": home,
        "db": db,
        "workspace": Path(os.environ.get("ANTHILL_WORKSPACE", "workspace")),
        "wikis": Path(os.environ.get("ANTHILL_WIKI_ROOT", "data/wikis")),
        "org_wiki": Path(os.environ.get("ANTHILL_ORG_WIKI", "data/org-wiki")),
        "org_wikis": org_wikis_base(),
        "files": Path(os.environ.get("ANTHILL_FILES_DIR", "data/files")),
        "skills": Path(os.environ.get("ANTHILL_SKILLS_DIR", "skills")),
        "secrets": home / "secrets.env",
    }


def ollama_models_dir() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models")))


def passphrase_from_env() -> str | None:
    """The backup passphrase for a caller that cannot ask: a file (``ANTHILL_BACKUP_PASSPHRASE_FILE``), else
    ``ANTHILL_BACKUP_PASSPHRASE``. An unreadable, oversized or empty file is an error, never a fall-through to
    the variable or the prompt. One line ending at the end of the file is not part of the passphrase."""
    file = os.environ.get(ENV_BACKUP_PASSPHRASE_FILE, "")
    if file:
        try:
            raw = Path(file).read_bytes()
            text = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        except (OSError, UnicodeDecodeError):
            raise BackupError("The passphrase file cannot be read.") from None
        if len(raw) > 4096 or not text.strip():
            raise BackupError("The passphrase file is empty or too large.")
        return text
    return os.environ.get(ENV_BACKUP_PASSPHRASE) or None


def check_new_passphrase(passphrase: str) -> None:
    """A passphrase for a NEW backup: at least MIN_PASSPHRASE characters. Restoring does not enforce this."""
    if len(passphrase.strip()) < MIN_PASSPHRASE:
        raise PassphraseTooShort(
            f"Choose a passphrase of at least {MIN_PASSPHRASE} characters. Without it this backup cannot be "
            "restored, and we cannot recover it for you."
        )


# ── archive helpers ───────────────────────────────────────────────────────────


def _sqlite_consistent_copy(src: Path, dest: Path) -> None:
    """Copy a (possibly live) SQLite DB via the backup API - never a torn read. The copy is made 0600."""
    os.close(os.open(dest, os.O_WRONLY | os.O_CREAT, 0o600))
    os.chmod(dest, 0o600)
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
        s.backup(d)


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _without_keys(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """No key file ever rides along in a tree (a data folder that is also the wiki folder, say)."""
    return None if Path(info.name).name in ("secrets.env", keystore.MARKER_FILE) else info


def _env_assignments(raw: bytes) -> dict[str, str]:
    """``NAME=value`` lines of an env file (``export`` and one pair of quotes allowed)."""
    found: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and _ENV_NAME.fullmatch(name.strip()):
            found[name.strip()] = value.strip().strip("\"'")
    return found


def _other_env(found: dict[str, str]) -> dict[str, str]:
    """The lines of ``secrets.env`` that are not the key pair (optional search and fetch keys that a bring-in
    copied there): main backed them up with the file, so the encrypted backup keeps them too."""
    return {k: v for k, v in found.items() if k not in _KEY_ENV and v}


def _key_export(secrets_env: Path) -> bytes:
    try:
        raw = secrets_env.read_bytes()[:_ENV_FILE_MAX] if secrets_env.is_file() else b""
    except OSError:
        raw = b""
    doc = {
        "version": 1,
        "data_key": base64.b64encode(keystore.data_key()).decode(),
        "jwt_secret": keystore.jwt_secret(),
        "env": _other_env(_env_assignments(raw)),
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(doc).encode()


# ── public API ────────────────────────────────────────────────────────────────


@dataclass
class BackupResult:
    path: Path
    bytes: int
    includes: list[str]


def create_backup(out_path: Path | str | None = None, *, passphrase: str) -> BackupResult:
    """Write one passphrase-protected ``.anthillbak`` of the whole data dir.

    A passphrase of at least MIN_PASSPHRASE characters is required: it is all that protects the key inside the
    archive. Model files are not part of a backup. Raises ``BackupError`` or ``keystore.KeystoreError`` (no key)."""
    check_new_passphrase(passphrase)
    return _create(out_path, passphrase)


def _create(out_path: Path | str | None, passphrase: str) -> BackupResult:
    p = data_paths()
    key_export = _key_export(p["secrets"])
    if out_path is None:
        backups = p["home"] / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        out_path = backups / f"anthill-backup-{_now_stamp()}{backup_crypto.EXTENSION}"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    includes = (["db"] if p["db"].is_file() else []) + [k for k, _ in _TREES if p[k].exists()]
    includes.append("keys")
    manifest = {
        "anthill_backup_format": BACKUP_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "includes": includes,
        "ollama_models": [],
        "protected": True,
        "note": "Protected with a passphrase. The key is inside the encrypted archive. Model files are not "
        "included.",
    }
    partial = out_path.with_name(out_path.name + ".partial")
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fp:
            writer = backup_crypto.EncryptingWriter(fp, passphrase)
            with tarfile.open(fileobj=writer, mode="w|gz") as tar:
                _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
                _add_bytes(tar, "data/key-export.json", key_export)
                if p["db"].is_file():
                    tmp_db = p["db"].with_suffix(p["db"].suffix + f".bkptmp-{_now_stamp()}")
                    try:
                        _sqlite_consistent_copy(p["db"], tmp_db)
                        tar.add(tmp_db, arcname="data/anthill.db")
                    finally:
                        tmp_db.unlink(missing_ok=True)
                for key, arc in _TREES:
                    if p[key].exists():
                        tar.add(p[key], arcname=arc, filter=_without_keys)
            writer.close()
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(partial, out_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return BackupResult(out_path, out_path.stat().st_size, includes)


@dataclass
class RestoreResult:
    restored: list[str]
    models: list[str]
    safety_backup: Path | None
    moved_aside: Path | None = (
        None  # the org-wikis folder set aside by a restore of an older archive
    )
    protected: bool = True  # False: an older plain archive, whose key was not protected
    restart_needed: bool = True  # the running process still holds the old database
    db_moved_aside: Path | None = None
    key_replaced: bool = False  # the install's key was swapped for the backup's
    key_moved_aside: Path | None = None  # a plain-file install: the previous secrets.env, kept
    warnings: list[str] = field(default_factory=list)


def archive_kind(archive_path: Path | str) -> str:
    """``"protected"`` (a ``.anthillbak``) or ``"plain"`` (an older ``.tar.gz``), told apart by their first bytes."""
    with open(archive_path, "rb") as fh:
        head = fh.read(8)
    if backup_crypto.is_protected(head):
        return "protected"
    if head[:2] == b"\x1f\x8b":
        return "plain"
    raise BackupError("This is not an Anthill backup.")


@dataclass
class _Staged:
    manifest: dict
    key_export: bytes | None
    secrets_env: bytes | None
    present: set[str]
    has_db: bool
    extra_env: dict[str, str] = field(
        default_factory=dict
    )  # the other lines of the archive's secrets.env


def _read_small(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    src = tar.extractfile(member)
    if src is None or member.size > _SMALL_MAX:
        raise BackupError("The backup holds an entry that is not valid.")
    return src.read()


def _wanted(name: str) -> bool:
    return (
        name == "data/anthill.db"
        or name.startswith("ollama/")
        or any(name == arc or name.startswith(arc + "/") for _, arc in _TREES)
    )


def _too_big() -> BackupError:
    return BackupError(
        "The backup is larger than this computer has room to unpack, or holds far too many files. "
        "Nothing was changed."
    )


def _listed_twice() -> BackupError:
    return BackupError(
        "The backup lists the same file twice, so it is not valid. Nothing was changed."
    )


def _safe_dest(staging: Path, name: str) -> Path:
    dest = staging / name
    if not dest.resolve().is_relative_to(staging.resolve()):
        raise BackupError(
            "The backup holds an entry that points outside itself. Nothing was changed."
        )
    return dest


class _Stager:
    """Reads an archive stream once into a staging folder, one member at a time (see ``_stage``)."""

    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.budget = int(shutil.disk_usage(staging).free * _FREE_SPACE_SHARE)
        self.written = 0
        self.members = 0
        self.seen: set[str] = set()
        self.manifest: object = None
        self.key_export: bytes | None = None
        self.secrets_env: bytes | None = None
        self.present: set[str] = set()
        self.has_db = False

    def read(self, tar: tarfile.TarFile) -> None:
        for m in tar:
            self.members += 1
            if self.members > _MAX_MEMBERS:
                raise _too_big()
            self._member(tar, m)

    def _member(self, tar: tarfile.TarFile, m: tarfile.TarInfo) -> None:
        name = m.name.rstrip("/")
        if name.startswith("/") or ".." in Path(name).parts:
            return
        if name in _SPECIAL:
            if m.isfile():
                self._special(tar, m, name)
            return
        if not _wanted(name) or not (m.isfile() or m.isdir()):
            return
        if any(name == arc for _, arc in _TREES) and not m.isdir():
            return  # a tree's own root must be a folder
        dest = _safe_dest(self.staging, name)
        if m.isdir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            self._file(tar, m, name, dest)
        for key, arc in _TREES:
            if name == arc or name.startswith(arc + "/"):
                self.present.add(key)
        self.has_db = self.has_db or name == "data/anthill.db"

    def _once(self, name: str) -> None:
        if name in self.seen:
            raise _listed_twice()
        self.seen.add(name)

    def _special(self, tar: tarfile.TarFile, m: tarfile.TarInfo, name: str) -> None:
        self._once(name)
        data = _read_small(tar, m)
        if name == "manifest.json":
            self.manifest = json.loads(data)
        elif name == "data/key-export.json":
            self.key_export = data
        else:
            self.secrets_env = data

    def _file(self, tar: tarfile.TarFile, m: tarfile.TarInfo, name: str, dest: Path) -> None:
        self._once(name)
        src = tar.extractfile(m)
        assert src is not None
        dest.parent.mkdir(parents=True, exist_ok=True)
        with src, open(dest, "xb") as out:
            while chunk := src.read(_COPY_CHUNK):
                self.written += len(chunk)
                if self.written > self.budget:
                    raise _too_big()
                out.write(chunk)


def _stage(source: BinaryIO, staging: Path) -> _Staged:
    """Read the archive stream once: files into ``staging``, the manifest and the keys into memory (a key is
    never written to disk here). Entries that are not plain files or folders, or that point outside the
    archive, are skipped. The read stops, failing closed, when the bytes written pass the staging budget
    (a share of the free space) or the entry count passes its cap, and a file listed twice is refused."""
    stager = _Stager(staging)
    try:
        with tarfile.open(fileobj=source, mode="r|gz") as tar:
            stager.read(tar)
        if isinstance(source, backup_crypto.DecryptingReader):
            source.drain()
    except (tarfile.TarError, EOFError, zlib.error, ValueError):
        raise BackupError(
            "The backup could not be read: it is damaged or not an Anthill backup. Nothing was changed."
        ) from None
    except OSError:
        raise BackupError(
            "The backup could not be unpacked here (is there enough free space?). Nothing was changed."
        ) from None
    manifest = stager.manifest
    if not isinstance(manifest, dict) or "anthill_backup_format" not in manifest:
        raise BackupError("This is not an Anthill backup (no manifest). Nothing was changed.")
    return _Staged(manifest, stager.key_export, stager.secrets_env, stager.present, stager.has_db)


def _keys_from_export(raw: bytes) -> tuple[bytes, str]:
    try:
        doc = json.loads(raw)
        key = base64.b64decode(doc["data_key"], validate=True)
        secret = doc["jwt_secret"]
        if doc["version"] != 1 or len(key) != 32 or not isinstance(secret, str) or not secret:
            raise ValueError
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise BackupError("The key inside the backup is not valid. Nothing was changed.") from None
    return key, secret


def _env_from_export(raw: bytes) -> dict[str, str]:
    env = json.loads(raw).get("env")
    if not isinstance(env, dict):
        return {}
    return {
        k: v
        for k, v in env.items()
        if isinstance(k, str)
        and _ENV_NAME.fullmatch(k)
        and k not in _KEY_ENV
        and isinstance(v, str)
        and v
    }


def _keys_from_env(raw: bytes) -> tuple[bytes | None, str | None]:
    """The key lines of an older plain archive's ``secrets.env``."""
    found = _env_assignments(raw)
    try:
        key = base64.b64decode(found.get(_KEY_ENV[0], ""), validate=True) or None
    except (binascii.Error, ValueError):
        key = None
    return (key if key and len(key) == 32 else None), found.get(_KEY_ENV[1]) or None


def read_manifest(archive_path: Path | str, passphrase: str | None = None) -> dict:
    """Return the manifest of a backup archive (raises if it isn't an Anthill backup). A protected archive
    needs its passphrase; its manifest is its first entry, so this does not verify the whole file."""
    protected = archive_kind(archive_path) == "protected"
    if protected and not passphrase:
        raise PassphraseNeeded("A passphrase is needed to read this backup.")
    with open(archive_path, "rb") as fh:
        source = backup_crypto.DecryptingReader(fh, passphrase or "") if protected else fh
        with tarfile.open(fileobj=source, mode="r|gz") as tar:
            for m in tar:
                if m.name == "manifest.json" and m.isfile():
                    return json.loads(_read_small(tar, m))
    raise BackupError("This is not an Anthill backup (no manifest).")


def _free(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


class _Undo:
    """What to put back if the apply step fails half way. Every step is registered BEFORE the change it
    undoes, so a change that fails part way (a move across volumes, say) is still cleaned up."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], object]]] = []

    def add(self, what: str, step: Callable[[], object]) -> None:
        self._steps.append((what, step))

    def set_aside(self, path: Path, stamp: str) -> Path:
        aside = _free(path.with_name(f"{path.name}.before-restore-{stamp}"))
        self.add(f"put {path.name} back from {aside.name}", lambda: aside.rename(path))
        path.rename(aside)
        return aside

    def place(self, staged: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            raise FileExistsError(dest.name)  # never remove something this restore did not make
        self.add(f"remove the partly restored {dest.name}", lambda: _remove(dest))
        shutil.move(str(staged), str(dest))

    def rollback(self) -> list[str]:
        """Undo everything, last step first, and carry on past a failure. Returns what could not be undone."""
        failed: list[str] = []
        while self._steps:
            what, step = self._steps.pop()
            try:
                step()
            except Exception:  # noqa: BLE001 - undo carries on past a failed step, to undo as much as it can
                failed.append(what)
        return failed


def _undo_failed(undo: _Undo, safety: Path | None) -> BackupError | None:
    """After a rollback: an error that names what could not be undone and where the safety backup is."""
    failed = undo.rollback()
    if not failed:
        return None
    where = f" A safety backup of the previous data is at {safety}." if safety else ""
    return BackupError(
        "The restore failed part way and these steps could not be undone: "
        + "; ".join(failed)
        + f".{where} The previous files, if any, are beside the current ones as *.before-restore-*."
    )


def _write_env_keys(path: Path, updates: dict[str, str], stamp: str, undo: _Undo) -> Path | None:
    """An install whose key is in a plain file: put the key lines into ``secrets.env``, keeping the other lines
    and a copy of the old file."""
    kept: Path | None = None
    lines: list[str] = []
    if path.is_file():
        copy = kept = _free(path.with_name(f"{path.name}.before-restore-{stamp}"))
        shutil.copy2(path, copy)
        lines = [
            ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.partition("=")[0].strip().removeprefix("export ").strip() not in updates
        ]
        undo.add(f"put {path.name} back", lambda: os.replace(copy, path))
    else:
        undo.add(f"remove the new {path.name}", lambda: path.unlink(missing_ok=True))
    lines += [f"{k}={v}" for k, v in updates.items()]
    tmp = path.with_name(f"{path.name}.tmp-{secrets.token_hex(4)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return kept


def _import_keys(
    data_key: bytes | None,
    jwt: str | None,
    p: dict[str, Path],
    stamp: str,
    undo: _Undo,
    result: RestoreResult,
) -> Callable[[], None] | None:
    """Make the backup's key this install's key. The same key: nothing to do. Different, with the key in the
    OS key store: ``keystore.adopt`` (a new item, the marker repointed, the old item kept until ``finish`` is called
    by the caller once everything worked, and put back by the undo list if not). Different, key in a plain
    file: the key lines go into ``secrets.env`` with a copy of the old file."""
    if not data_key:
        result.warnings.append(
            "The backup carries no key, so this install's key was left as it is."
        )
        return None
    current: bytes | None = None
    current_jwt: str | None = None
    try:
        current, current_jwt = keystore.data_key(), keystore.jwt_secret()
    except keystore.KeystoreError:
        pass  # the recovery case: the install's own key is gone
    if current is not None and secrets.compare_digest(current, data_key):
        return None
    jwt = jwt or current_jwt or secrets.token_urlsafe(48)
    result.restored.append("keys")
    if keystore.marker_exists():
        try:
            finish, undo_adopt = keystore.adopt(data_key, jwt)
        except keystore.KeystoreError:
            raise BackupError(
                "The backup's key could not be stored on this computer. Nothing was changed."
            ) from None
        undo.add("put the previous key back", undo_adopt)
        result.key_replaced = True
        return finish
    updates = {_KEY_ENV[0]: base64.b64encode(data_key).decode(), _KEY_ENV[1]: jwt}
    result.key_moved_aside = _write_env_keys(p["secrets"], updates, stamp, undo)
    result.key_replaced = True
    return None


def _check_key_opens_everything(
    data_key: bytes | None, staged_db: Path, allow: bool, result: RestoreResult
) -> None:
    """The archive's key must open EVERY stored value in the archive's database, checked before anything is
    overwritten. With no key at all, a database holding values is refused. ``allow`` accepts either, knowingly."""
    try:
        if data_key is None:
            ok = not stored_values.holds_values(staged_db)
        else:
            ok = stored_values.all_open(data_key, staged_db)
    except stored_values.TooManyValues:
        raise BackupError(
            f"This backup holds more than {stored_values.CAP} saved passwords, too many to check against its "
            "key, so it was not restored. Nothing was changed."
        ) from None
    except keystore.ValuesUnreadable:
        raise BackupError(
            "The database inside this backup cannot be read. Nothing was changed."
        ) from None
    if ok:
        return
    if not allow:
        raise BackupError(
            "This backup has no key. Restoring it would make the stored passwords and tokens unreadable. "
            "Nothing was changed."
            if data_key is None
            else "The key in this backup does not open every saved password in it, so it was not restored. "
            "Nothing was changed."
        )
    result.warnings.append(
        "Some saved passwords and tokens in this backup will not open after the restore."
    )


def restore_backup(
    archive_path: Path | str,
    *,
    passphrase: str | None = None,
    make_safety_backup: bool = True,
    allow_without_key: bool = False,
) -> RestoreResult:
    """Reload a backup archive into this install's data dir (and Ollama store for an old archive's model).

    The archive is read into a staging folder beside the data and verified whole (every chunk, the final
    flag) BEFORE anything current is touched, and checked as a unit: it must hold a database, and its key must
    open every stored encrypted value in it. A wrong passphrase, a damaged or cut-short archive, or an
    incomplete one raises and leaves the install exactly as it was. Only then is a safety copy taken
    (protected with the same passphrase) and the new state put in place: the old database is moved aside as
    ``*.before-restore-<time>``. If putting it in place fails, everything is put back and the safety copy is
    kept; what could not be put back is named in the error. ``allow_without_key=True`` accepts an old archive
    with no key, or whose key does not open every value, knowing the stored passwords will not open. An older
    plain ``.tar.gz`` still restores; ``protected`` is False on its result. Restart the app afterwards."""
    archive_path = Path(archive_path)
    p = data_paths()
    protected = archive_kind(archive_path) == "protected"
    safety_wanted = make_safety_backup and p["db"].is_file()
    if (protected or safety_wanted) and not (passphrase and passphrase.strip()):
        raise PassphraseNeeded(
            "A passphrase is needed: it opens the backup, or protects the safety copy of the current data."
            if protected
            else "A passphrase is needed to protect the safety copy of the current data."
        )
    stamp = _now_stamp()
    result = RestoreResult([], [], None, protected=protected)
    with open(archive_path, "rb") as fh:
        source: BinaryIO = (
            backup_crypto.DecryptingReader(fh, passphrase or "") if protected else fh  # type: ignore[assignment]
        )
        staging = p["home"] / f".restore-staging-{stamp}-{secrets.token_hex(3)}"
        staging.mkdir(parents=True, mode=0o700)
        os.chmod(staging, 0o700)
        _staging_in_use.add(staging)
        try:
            staged = _stage(source, staging)
            data_key = jwt = None
            if protected:
                if staged.key_export is None:
                    raise BackupError("The backup holds no key, so it cannot be restored.")
                data_key, jwt = _keys_from_export(staged.key_export)
                staged.extra_env = _env_from_export(staged.key_export)
            else:
                result.warnings.append(
                    "This backup is not protected: its key was stored unprotected, and anyone who held the "
                    "file could read it. Make a new protected backup now."
                )
                if staged.secrets_env is not None:
                    data_key, jwt = _keys_from_env(staged.secrets_env)
                    staged.extra_env = _other_env(_env_assignments(staged.secrets_env))
            if not staged.has_db:
                raise BackupError(
                    "This backup holds no database, so it cannot be restored as a whole. Nothing was changed."
                )
            staged_db = staging / "data" / "anthill.db"
            os.chmod(staged_db, 0o600)
            _check_key_opens_everything(data_key, staged_db, allow_without_key, result)
            if safety_wanted:
                safety = p["home"] / "backups" / f"pre-restore-{stamp}{backup_crypto.EXTENSION}"
                try:
                    result.safety_backup = _create(safety, passphrase or "").path
                except keystore.KeystoreError:
                    result.warnings.append(
                        "The current key could not be read, so no safety copy was made. The old database "
                        "is kept aside."
                    )
            # The safety copy is kept on every failure from here: it is the way back.
            _apply(staged, staging, p, stamp, data_key, jwt, result)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            _staging_in_use.discard(staging)
    result.models = (
        [str(m) for m in staged.manifest.get("ollama_models") or []] if result.restored else []
    )
    if result.models:
        result.restored.append("ollama_models")
    result.restart_needed = bool(result.restored)
    return result


def _apply_keys(
    staged: _Staged,
    p: dict[str, Path],
    stamp: str,
    data_key: bytes | None,
    jwt: str | None,
    undo: _Undo,
    result: RestoreResult,
) -> Callable[[], None] | None:
    finish_key = _import_keys(data_key, jwt, p, stamp, undo, result)
    if staged.extra_env:  # optional API keys that lived in secrets.env: back as main restored them
        kept = _write_env_keys(p["secrets"], staged.extra_env, stamp, undo)
        result.key_moved_aside = result.key_moved_aside or kept
    return finish_key


def _apply_db(
    staging: Path, p: dict[str, Path], stamp: str, undo: _Undo, result: RestoreResult
) -> None:
    db = p["db"]
    for suffix in ("-wal", "-shm", "-journal"):
        side = db.with_name(db.name + suffix)
        if side.exists():
            undo.set_aside(side, stamp)
    if db.exists():
        result.db_moved_aside = undo.set_aside(db, stamp)
    undo.place(staging / "data" / "anthill.db", db)
    result.restored.append("db")


def _apply_trees(
    staged: _Staged,
    staging: Path,
    p: dict[str, Path],
    stamp: str,
    undo: _Undo,
    asides: list[Path],
    result: RestoreResult,
) -> None:
    # Trees: wipe-and-replace so a restore is a true point-in-time, not a merge.
    for key, arc in _TREES:
        if key not in staged.present:
            continue
        if p[key].exists():
            asides.append(undo.set_aside(p[key], stamp))
        undo.place(staging / arc, p[key])
        result.restored.append(key)
    # An archive made before the org wiki was stored per organisation has org-wiki but no org-wikis.
    # The org-wikis already on this machine belong to a later state than the restored org-wiki, so
    # leave them out of the live path (moved aside, not deleted) rather than mix the two states.
    if (
        "org_wiki" in result.restored
        and "org_wikis" not in result.restored
        and p["org_wikis"].exists()
    ):
        result.moved_aside = undo.set_aside(p["org_wikis"], stamp)
        result.restored.append("org_wikis_moved_aside")


def _apply_models(staging: Path, stamp: str, undo: _Undo, asides: list[Path]) -> None:
    """Model files from an older plain archive: an existing file is set aside first, and every move is
    under the undo list like the rest."""
    models = staging / "ollama"
    if not models.is_dir():
        return
    base = ollama_models_dir()
    for f in (f for f in models.rglob("*") if f.is_file()):
        dest = base / f.relative_to(models)
        if dest.exists():
            asides.append(undo.set_aside(dest, stamp))
        undo.place(f, dest)


def _apply(
    staged: _Staged,
    staging: Path,
    p: dict[str, Path],
    stamp: str,
    data_key: bytes | None,
    jwt: str | None,
    result: RestoreResult,
) -> None:
    undo = _Undo()
    asides: list[Path] = []
    try:
        finish_key = _apply_keys(staged, p, stamp, data_key, jwt, undo, result)
        _apply_db(staging, p, stamp, undo, result)
        _apply_trees(staged, staging, p, stamp, undo, asides, result)
        _apply_models(staging, stamp, undo, asides)
    except BaseException as exc:
        stuck = _undo_failed(undo, result.safety_backup)
        if stuck is not None:
            raise stuck from exc
        raise
    # Everything is in place: the old trees, replaced model files and the previous key item are no longer
    # needed. The previous database stays aside.
    for aside in asides:
        _remove(aside)
    if finish_key:
        finish_key()


def snapshot_db(reason: str = "manual", *, keep: int = 7) -> Path | None:
    """Fast pre-migration copy of the DB into ``<home>/snapshots``; prune to the last N.

    Carries no key: a snapshot is restored on the same machine, where the key layer is already in place. Cheap
    enough to call before every schema migration; the safety net for a bad/aborted migration."""
    p = data_paths()
    if not p["db"].is_file():
        return None
    snaps = p["home"] / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    os.chmod(snaps, 0o700)
    dest = snaps / f"anthill-{_now_stamp()}-{reason}.db"
    _sqlite_consistent_copy(p["db"], dest)
    # prune oldest, keeping the most recent `keep` (and the key file an older version left beside one)
    for old in sorted(snaps.glob("anthill-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
        old.with_name(old.name + ".secrets.env").unlink(missing_ok=True)
    return dest


def restore_snapshot(snapshot: Path | str) -> RestoreResult:
    """Put a pre-migration snapshot back as the database. Never asks for a passphrase: the key on this machine
    is what opens it. The current database is moved aside, not overwritten. If this install's key does not open
    every saved password in the snapshot, the restore still happens and a warning says so."""
    snapshot = Path(snapshot)
    p = data_paths()
    result = RestoreResult([], [], None)
    try:
        with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as con:
            ok = con.execute("PRAGMA quick_check").fetchone() == ("ok",)
    except sqlite3.Error:
        ok = False
    if not ok:
        raise BackupError("This snapshot is not a readable database. Nothing was changed.")
    try:
        if not stored_values.all_open(keystore.data_key(), snapshot):
            result.warnings.append(
                "Some saved passwords in this snapshot do not open with this install's key. They may need "
                "entering again."
            )
    except keystore.KeystoreError:
        result.warnings.append("This install's key could not be checked against the snapshot.")
    stamp = _now_stamp()
    undo = _Undo()
    tmp = p["db"].with_name(f"{p['db'].name}.restore-{secrets.token_hex(3)}")
    try:
        p["db"].parent.mkdir(parents=True, exist_ok=True)
        _sqlite_consistent_copy(snapshot, tmp)
        for suffix in ("-wal", "-shm", "-journal"):
            side = p["db"].with_name(p["db"].name + suffix)
            if side.exists():
                undo.set_aside(side, stamp)
        if p["db"].exists():
            result.db_moved_aside = undo.set_aside(p["db"], stamp)
        undo.place(tmp, p["db"])
    except BaseException as exc:
        stuck = _undo_failed(undo, None)
        if stuck is not None:
            raise stuck from exc
        raise
    finally:
        tmp.unlink(missing_ok=True)
    result.restored.append("db")
    return result


def sweep_stale_staging() -> list[str]:
    """Remove ``.restore-staging-*`` folders in the data home that a crashed restore left behind (they can hold
    a whole unpacked backup, keys excluded). Only real folders named exactly like that, directly inside the data
    home, not one this process is using and not one young enough to belong to a restore in another process."""
    home = data_paths()["home"]
    removed: list[str] = []
    try:
        root, entries = home.resolve(), list(home.iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            if (
                _STAGING.fullmatch(entry.name)
                and entry.is_dir()
                and not entry.is_symlink()
                and entry.resolve().parent == root
                and entry not in _staging_in_use
                and time.time() - entry.stat().st_mtime >= _STAGING_MIN_AGE
            ):
                shutil.rmtree(entry)
                removed.append(entry.name)
        except OSError:
            continue
    return removed
