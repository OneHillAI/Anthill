"""IMAP push worker: pulls new mail into the workspace ``inbox/`` in near real-time.

It holds a connection to the configured mailbox and waits for new messages using
**IMAP IDLE** when the runtime supports it (``imaplib.IMAP4.idle()``, Python 3.13+),
falling back to a short periodic re-check otherwise. New (UNSEEN) messages are
written to ``inbox/`` via :mod:`ingest_push`, marked Seen, and the scheduler is
woken so the event tick drains them immediately.

The connection boundary is injectable (``factory``) so the login / fetch / write
logic is unit-testable without a live mailbox. The live IDLE socket path needs
validation against a real IMAP server; the poll fallback runs on 3.10/3.11.
"""

from __future__ import annotations

import imaplib
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from . import ingest_push
from .db import OrgSettings

_worker_started = False
_RECHECK_S = 20  # poll-fallback cadence + IDLE re-arm safety net


def _decrypt(enc: str) -> str:
    if not enc:
        return ""
    try:
        from .crypto import decrypt

        return decrypt(enc)
    except Exception:
        return ""


def first_imap_org(db):
    """The first org with IMAP enabled and a host set, or ``None``."""
    return (
        db.query(OrgSettings)
        .filter(OrgSettings.imap_enabled.is_(True), OrgSettings.imap_host != "")
        .order_by(OrgSettings.org_id)
        .first()
    )


def connect(host: str, port: int, user: str, password: str, *, factory=None):
    """Open an authenticated IMAP connection. ``factory(host, port)`` returns the
    client (defaults to ``imaplib.IMAP4_SSL``); injectable for tests."""
    factory = factory or imaplib.IMAP4_SSL
    client = factory(host, int(port or 993))
    client.login(user, password)
    return client


def test_connection(
    host, port, user, password, folder="INBOX", *, factory=None
) -> tuple[bool, str]:
    """Validate the mailbox config: login + SELECT the folder, then log out.

    Returns ``(ok, detail)``; never raises.
    """
    if not (host and user and password):
        return False, "host, user, and password are required"
    try:
        client = connect(host, port, user, password, factory=factory)
        try:
            typ, _ = client.select(folder or "INBOX", readonly=True)
            if typ != "OK":
                return False, f"cannot open folder {folder!r}"
            return True, f"connected to {host} ({folder or 'INBOX'})"
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def fetch_unseen(client, folder: str = "INBOX") -> list[bytes]:
    """SELECT the folder, fetch every UNSEEN message's raw bytes, and mark it Seen."""
    out: list[bytes] = []
    typ, _ = client.select(folder or "INBOX")
    if typ != "OK":
        return out
    typ, data = client.search(None, "UNSEEN")
    if typ != "OK" or not data or not data[0]:
        return out
    for num in data[0].split():
        typ, msg_data = client.fetch(num, "(RFC822)")
        if typ != "OK" or not msg_data:
            continue
        raw = next((part[1] for part in msg_data if isinstance(part, tuple) and part[1]), None)
        if raw:
            out.append(raw if isinstance(raw, bytes) else bytes(raw))
            client.store(num, "+FLAGS", "\\Seen")
    return out


def _write_unseen(client, folder: str, *, on_event=None) -> int:
    """Write any UNSEEN mail into ``inbox/`` and return how many were written.

    ``on_event`` (the scheduler's ``signal_event``) is called once if anything was
    written, so the drain happens immediately instead of on the next tick.
    """
    ws_path = os.environ.get("ANTHILL_WORKSPACE", "workspace")
    inbox_dir = os.path.join(ws_path, "inbox")
    written = 0
    for raw in fetch_unseen(client, folder):
        title, body = ingest_push.email_to_doc(ingest_push.parse_email_bytes(raw))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        ingest_push.write_event(inbox_dir, "email", title, body, stamp=stamp)
        written += 1
    if written and on_event:
        on_event()
    return written


def drain_once(cfg, *, factory=None, on_event=None) -> int:
    """One connect -> write UNSEEN -> logout cycle. Returns the count written."""
    client = connect(
        cfg.imap_host,
        cfg.imap_port,
        cfg.imap_user,
        _decrypt(cfg.imap_password_enc),
        factory=factory,
    )
    try:
        return _write_unseen(client, cfg.imap_folder or "INBOX", on_event=on_event)
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _idle_or_sleep(client, timeout: int) -> None:
    """Block until the server signals activity or ``timeout`` passes. Uses IMAP IDLE
    when available (Python 3.13+), else a plain sleep (poll fallback)."""
    idle = getattr(client, "idle", None)
    if idle is None:
        time.sleep(timeout)
        return
    try:
        with idle(timeout) as responses:  # 3.13+ imaplib context manager
            for _ in responses:
                break  # woke on a server update; return to drain
    except Exception:
        time.sleep(timeout)


def _set_status(Session, org_id: int, status: str, detail: str) -> None:
    db = Session()
    try:
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if cfg:
            cfg.imap_status, cfg.imap_status_detail = status, detail[:255]
            db.commit()
    except OperationalError:
        pass  # schema not ready yet (see _config_snapshot); a status write is best-effort
    finally:
        db.close()


def _config_snapshot(Session):
    """A plain tuple of the active IMAP config, read + released so the long-lived
    worker never holds a DB session open across an IDLE wait."""
    db = Session()
    try:
        cfg = first_imap_org(db)
        if cfg is None:
            return None
        return (
            cfg.org_id,
            cfg.imap_host,
            cfg.imap_port,
            cfg.imap_user,
            _decrypt(cfg.imap_password_enc),
            cfg.imap_folder or "INBOX",
        )
    except OperationalError:
        # First-boot race: the worker can start before create_all / ensure_columns finish, so the
        # org_settings table (or a newly added column) may not exist yet. This snapshot is the ONLY
        # DB read outside the loop's try/except, so an unhandled error here would kill the whole IMAP
        # thread until the next restart. Treat an unready schema as "nothing configured yet"; the loop
        # retries every _RECHECK_S and picks the config up once the schema is ready.
        return None
    finally:
        db.close()


def _loop(engine):
    from .scheduler import signal_event

    Session = sessionmaker(bind=engine)
    while True:
        snap = _config_snapshot(Session)
        if snap is None:
            time.sleep(_RECHECK_S)  # nothing configured yet; idle cheaply
            continue
        org_id, host, port, user, pw, folder = snap
        client = None
        try:
            client = connect(host, port, user, pw)
            _set_status(Session, org_id, "connected", "watching for new mail")
            while True:
                _write_unseen(client, folder, on_event=signal_event)
                cur = _config_snapshot(Session)  # config changed/disabled? reconnect
                if cur is None or cur[:5] != snap[:5]:
                    break
                _idle_or_sleep(client, _RECHECK_S)
        except Exception as exc:
            _set_status(Session, org_id, "error", f"{type(exc).__name__}: {exc}")
            time.sleep(min(300, _RECHECK_S * 3))  # backoff before reconnecting
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass


def start_worker(engine) -> None:
    """Start the IMAP push worker as a daemon thread (idempotent). A no-op until an
    org enables IMAP, so it's always safe to start at boot."""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_loop, args=(engine,), daemon=True, name="anthill-imap").start()
