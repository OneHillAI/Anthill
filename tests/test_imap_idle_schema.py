"""The IMAP idle worker must survive an unready schema at first boot.

On a fresh install the worker can start before create_all / ensure_columns finish, so the
org_settings table (or a newly added column) may not exist yet. The worker's first config read is
the ONLY DB access outside its loop's try/except, so an unhandled OperationalError there kills the
whole anthill-imap daemon thread for the session (the dead-on-launch smoke test surfaced this as a
`no such table: org_settings` / `no such column: org_settings.vision_autopull` traceback). It must
degrade to "nothing configured yet" and retry, not crash.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import imap_idle


def _engine(tmp_path, *, create: bool):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    if create:
        db_mod.create_tables(eng)
    return eng


def test_config_snapshot_survives_unready_schema(tmp_path):
    # No tables created -> querying org_settings raises OperationalError. The snapshot must return
    # None (so the loop idles + retries), NOT raise (which would kill the worker thread on boot).
    Session = sessionmaker(bind=_engine(tmp_path, create=False))
    assert imap_idle._config_snapshot(Session) is None


def test_config_snapshot_none_when_no_imap_org(tmp_path):
    # Schema present, no IMAP-enabled org -> None (unchanged behaviour, still no crash).
    Session = sessionmaker(bind=_engine(tmp_path, create=True))
    assert imap_idle._config_snapshot(Session) is None


def test_set_status_survives_unready_schema(tmp_path):
    # A best-effort status write during the unready window must not raise either.
    Session = sessionmaker(bind=_engine(tmp_path, create=False))
    imap_idle._set_status(Session, 1, "connected", "ok")  # no assertion needed: must not raise
