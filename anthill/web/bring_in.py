"""Bring an earlier copy's account and data into a new, empty install.

Spec: docs/specs/first-run-bring-in-existing-data.md.

A copy run from a project folder (``start.sh``, ``make alpha``) keeps its database, wikis and secrets
inside that folder. The installed desktop app keeps them in its own data home, so a person who then
downloads the app finds no account. ``POST /setup/bring-in`` copies the old data across, once, on an
install that has no organisation yet.

The folder comes from a web request, so it is treated as untrusted: the install must be empty, the request
must come from the machine itself, only a fixed list of names under the old folder is read, and nothing is
followed out of it. The old folder is never changed, and nothing in the new install is overwritten.

Behind a proxy or tunnel the request is refused: ``request_is_local`` rejects any forwarding header and a
non-loopback client or ``Host``, and a server that is not marked ``ANTHILL_LOCAL_ONLY=1`` counts as shared.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from ..backup import data_paths
from . import crypto
from . import db as db_mod
from .db import Organization
from .install_scope import request_is_local, server_is_shared

log = logging.getLogger("anthill.bring_in")

SECRET_NAMES = (
    "ANTHILL_JWT_SECRET",
    "ANTHILL_ENCRYPTION_KEY",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_SEARCH_CX",
    "FIRECRAWL_API_KEY",
    "JINA_API_KEY",
    "MEILI_URL",
    "MEILI_API_KEY",
)
_ENV_READ_LIMIT = 64 * 1024
_STARTUP_STEPS = (
    "_ensure_install_owner",
    "_claim_legacy_org_wiki",
    "_migrate_legacy_personal_wiki",
)

LOGIN_NOTICES = {
    "1": "Your account and files from the earlier copy are here. Sign in with the email and password "
    "you used there.",
    "keys": "Your account and files from the earlier copy are here. Sign in with the email and password "
    "you used there. Its saved keys were not found, so passwords and API keys you stored in it "
    "need entering again.",
}

_REFUSED = (
    "Bringing in an earlier copy only works on a new install, from this computer. "
    "Open Anthill on the machine itself and try again."
)

_busy = threading.Lock()


class BringInError(Exception):
    """A reason, in words the person can act on, why nothing was brought in."""


class NeedsConfirmation(Exception):
    """The earlier copy's encryption key was not found; carry on only if the person says so."""


@dataclass
class _Old:
    data: Path
    base: Path

    def trees(self) -> list[tuple[str, Path]]:
        return [
            ("workspace", self.base / "workspace"),
            ("wikis", self.data / "wikis"),
            ("org_wiki", self.data / "org-wiki"),
            ("org_wikis", self.data / "org-wikis"),
            ("files", self.data / "files"),
            ("skills", self.data / "skills"),
        ]

    def env_files(self) -> list[Path]:
        return [self.base / ".env", self.data / "secrets.env"]


def page_context(request: Request, is_first_run: bool) -> dict:
    """What the first-run page needs to show the bring-in section."""
    from . import app as web_app

    db = web_app._db()
    try:
        available = is_first_run and _allowed(db, request)
    finally:
        db.close()
    return {
        "bring_in_available": available,
        "bring_in_home": str(data_paths()["home"]) if available else "",
        "bring_in_error": "",
        "bring_in_folder": "",
        "bring_in_confirm_keys": False,
    }


def login_notice(request: Request) -> str:
    return LOGIN_NOTICES.get(request.query_params.get("brought_in", ""), "")


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == request.headers.get("host", "")


def _allowed(db, request: Request) -> bool:
    return (
        db.query(Organization.id).first() is None
        and not server_is_shared(db)
        and request_is_local(request)
    )


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _guard(root: Path, path: Path) -> None:
    if not _inside(root, path.resolve()):
        raise BringInError(
            "Something in that folder points outside it, so nothing was brought in. "
            "Choose the folder Anthill was started from."
        )


def _locate(folder: str, home: Path) -> _Old:
    raw = folder.strip()
    if not raw:
        raise BringInError("Enter the folder of your earlier copy.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise BringInError("Enter the full path of the folder, starting with / or ~.")
    path = path.resolve()
    if not path.is_dir():
        raise BringInError("That is not a folder on this Mac.")
    if _inside(home.resolve(), path):
        raise BringInError("That is the folder this app already uses. Choose your earlier copy.")
    if (path / "data" / "anthill.db").is_file():
        old = _Old(data=path / "data", base=path)
    elif (path / "anthill.db").is_file():
        old = _Old(data=path, base=path.parent if path.name == "data" else path)
    else:
        raise BringInError(
            "No Anthill account was found there. Choose the folder you started Anthill from "
            "(it holds a data folder), or that data folder."
        )
    _guard(old.base, old.data / "anthill.db")
    return old


def _open_readonly(db_file: Path) -> sqlite3.Connection:
    """Read the old database without writing to its folder. A running or crashed copy has a ``-wal`` file
    that must be read through (``mode=ro``); a cleanly closed one is opened ``immutable`` so SQLite
    creates no sidecar files next to it."""
    mode = "mode=ro" if Path(f"{db_file}-wal").exists() else "immutable=1"
    return sqlite3.connect(f"{db_file.as_uri()}?{mode}", uri=True)


def _check_is_anthill(con: sqlite3.Connection) -> None:
    not_anthill = BringInError(
        "That file is not an Anthill database, or it holds no account to bring in."
    )
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"organizations", "users"} <= tables:
            raise not_anthill
        if con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
            raise not_anthill
    except sqlite3.DatabaseError:
        raise not_anthill


def _stage_database(source: Path, stage: Path) -> Path:
    staged = stage / "anthill.db"
    with closing(_open_readonly(source)) as src:
        _check_is_anthill(src)
        with closing(sqlite3.connect(staged)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise BringInError(
                    "The earlier copy's database is damaged, so it was not brought in."
                )
    for suffix in ("-wal", "-shm"):
        Path(f"{staged}{suffix}").unlink(missing_ok=True)
    os.chmod(staged, 0o600)
    return staged


def _parse_env(text: str, names: tuple[str, ...] | None = SECRET_NAMES) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if sep and value and (names is None or key in names):
            out[key] = value
    return out


def _read_secrets(old: _Old) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in old.env_files():
        if not path.is_file():
            continue
        _guard(old.base, path)
        with open(path, encoding="utf-8", errors="replace") as fh:
            found.update(_parse_env(fh.read(_ENV_READ_LIMIT)))
    key = found.get("ANTHILL_ENCRYPTION_KEY")
    if key is not None:
        try:
            valid = len(base64.b64decode(key, validate=True)) == 32
        except (binascii.Error, ValueError):
            valid = False
        if not valid:
            del found["ANTHILL_ENCRYPTION_KEY"]
    return found


def _copy_tree(src: Path, dst: Path, root: Path) -> None:
    dst.mkdir()
    for current, dirs, files in os.walk(src, followlinks=False):
        here = Path(current)
        target = dst / here.relative_to(src)
        for name in dirs + files:
            if (here / name).is_symlink():
                _guard(root, here / name)
        for name in dirs:
            if not (here / name).is_symlink():
                (target / name).mkdir()
        for name in files:
            path = here / name
            if path.is_file() and not path.is_symlink():
                shutil.copy2(path, target / name)


def _merge(staged: Path, dest: Path, undo: list[Callable[[], None]]) -> int:
    """Move the staged tree into ``dest``, leaving any file already there alone. Returns how many were left.
    Every change is recorded on ``undo`` so a later failure can put ``dest`` back as it was."""
    if not dest.exists() or (dest.is_dir() and not any(dest.iterdir())):
        was_there = dest.exists()
        os.replace(staged, dest)

        def drop_tree() -> None:
            shutil.rmtree(dest, ignore_errors=True)
            if was_there:
                dest.mkdir(exist_ok=True)

        undo.append(drop_tree)
        return 0
    kept = 0
    made_files: list[Path] = []
    made_dirs: list[Path] = []
    for current, _dirs, files in os.walk(staged):
        here = Path(current)
        target = dest / here.relative_to(staged)
        if not target.exists():
            made_dirs.append(target)
            target.mkdir(parents=True)
        for name in files:
            if (target / name).exists() or (target / name).is_symlink():
                kept += 1
            else:
                os.replace(here / name, target / name)
                made_files.append(target / name)

    def drop_files() -> None:
        for path in made_files:
            path.unlink(missing_ok=True)
        for path in sorted(made_dirs, key=lambda d: len(d.parts), reverse=True):
            with suppress(OSError):
                path.rmdir()

    undo.append(drop_files)
    return kept


def _write_secrets(path: Path, found: dict[str, str], undo: list[Callable[[], None]]) -> None:
    before = path.read_bytes() if path.is_file() else None
    saved = _parse_env(before.decode("utf-8"), None) if before is not None else {}
    saved.update(found)
    tmp = path.with_name(f"{path.name}.bring-in")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("".join(f"{k}={v}\n" for k, v in saved.items()))
    os.replace(tmp, path)

    def put_back() -> None:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(before)

    undo.append(put_back)


def _adopt_secrets(found: dict[str, str]) -> None:
    """Make the running process use the earlier copy's keys, so its encrypted fields read at once."""
    os.environ.update(found)
    if "ANTHILL_ENCRYPTION_KEY" in found:
        crypto._KEY = base64.b64decode(found["ANTHILL_ENCRYPTION_KEY"])
    if "ANTHILL_JWT_SECRET" in found:
        crypto._JWT_SECRET = found["ANTHILL_JWT_SECRET"]


def _swap_database(web_app, staged: Path, dest: Path) -> None:
    stale = web_app._engine
    web_app._engine = None
    web_app._SessionFactory = None
    if stale is not None:
        stale.dispose()
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{dest}{suffix}").unlink(missing_ok=True)
        os.replace(staged, dest)
    finally:
        db_mod.DB_PATH = dest
        raced = web_app._engine
        web_app._engine = None
        web_app._SessionFactory = None
        if raced is not None:
            raced.dispose()
        web_app._ensure_engine()


def bring_in(folder: str, *, without_keys: bool) -> bool:
    """Copy the earlier copy at ``folder`` into this install. True when its encryption key came along."""
    from . import app as web_app

    web_app._ensure_engine()
    engine = web_app._engine
    assert engine is not None
    engine_file = engine.url.database
    if not engine_file or engine_file == ":memory:":
        raise BringInError(
            "This install does not keep its data in a file, so nothing can be brought in."
        )
    paths = data_paths()
    dest_db = Path(engine_file)
    old = _locate(folder, paths["home"])

    stages: list[Path] = []
    undo: list[Callable[[], None]] = []

    def stage_beside(dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        stages.append(Path(tempfile.mkdtemp(prefix=".bring-in-", dir=dest.parent)))
        return stages[-1]

    try:
        staged_db = _stage_database(old.data / "anthill.db", stage_beside(dest_db))
        secrets = _read_secrets(old)
        if "ANTHILL_ENCRYPTION_KEY" not in secrets and not without_keys:
            raise NeedsConfirmation
        staged_trees: list[tuple[Path, Path]] = []
        for key, source in old.trees():
            if not source.is_dir():
                continue
            _guard(old.base, source)
            stage = stage_beside(paths[key])
            _copy_tree(source, stage / "tree", old.base)
            staged_trees.append((stage / "tree", paths[key]))

        for staged, dest in staged_trees:
            _merge(staged, dest, undo)
        if secrets:
            _write_secrets(paths["secrets"], secrets, undo)
        _swap_database(web_app, staged_db, dest_db)
        _adopt_secrets(secrets)
    except (OSError, sqlite3.Error) as exc:
        log.exception("bring-in failed")
        for step in reversed(undo):
            with suppress(OSError):
                step()
        raise BringInError(
            f"Could not copy from that folder ({type(exc).__name__}). Nothing in this app was changed."
        )
    finally:
        for stage in stages:
            shutil.rmtree(stage, ignore_errors=True)

    for name in _STARTUP_STEPS:
        try:
            getattr(web_app, name)()
        except Exception:
            log.exception("%s failed after bring-in", name)
    log.info("brought in an earlier copy (%s)", ", ".join(k for k, p in old.trees() if p.is_dir()))
    return "ANTHILL_ENCRYPTION_KEY" in secrets


def _setup_page(request: Request, status: int = 200, **bring_in_state):
    from . import app as web_app

    ctx = web_app._sso_ctx()
    resp = web_app.templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "error": "",
            "setup_step": 1,
            "is_first_run": True,
            "signup_closed": False,
            **page_context(request, True),
            **bring_in_state,
            **ctx,
        },
        status_code=status,
    )
    web_app._set_oauth_state(resp, ctx["oauth_state"])
    return resp


def bring_in_post(request: Request, folder: str = Form(""), without_keys: str = Form("")):
    from . import app as web_app

    if not _busy.acquire(blocking=False):
        return _setup_page(
            request, 409, bring_in_error="Another bring-in is running. Wait for it, then retry."
        )
    try:
        db = web_app._db()
        try:
            allowed = _allowed(db, request) and _same_origin(request)
        finally:
            db.close()
        if not allowed:
            return PlainTextResponse(_REFUSED, status_code=403)
        try:
            keys_came_along = bring_in(folder, without_keys=bool(without_keys))
        except BringInError as exc:
            return _setup_page(request, 400, bring_in_error=str(exc), bring_in_folder=folder)
        except NeedsConfirmation:
            return _setup_page(request, bring_in_confirm_keys=True, bring_in_folder=folder)
    finally:
        _busy.release()
    return RedirectResponse(
        f"/login?brought_in={'1' if keys_came_along else 'keys'}", status_code=302
    )
