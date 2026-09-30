"""SQLite hardening: WAL journal, the isolation-filter indexes, a private (0600) database file, and a
best-effort report of any pre-existing foreign-key violations.

Note on foreign keys: SQLite leaves enforcement OFF by default, so the ~70 declared foreign keys are
ORM wiring, not enforced constraints. Turning enforcement on is a separate, larger change (it needs
ON DELETE rules and a data clean-up), tracked apart from this. These tests lock the current, shipped
state: WAL is on, the isolation indexes exist, and the db file is not world-readable.
"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest
from sqlalchemy import text

from anthill.web import db as m


@pytest.fixture
def eng(tmp_path):
    e = m.get_engine(tmp_path / "t.db")
    m.create_tables(e)
    return e


def test_wal_journal_is_enabled(eng):
    with eng.begin() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_isolation_filter_uses_an_index(eng):
    """The planner must SEARCH via the index, not SCAN the table, for the org+user filter every
    owner-scoped read performs."""
    with eng.begin() as c:
        plan = str(
            c.execute(
                text("EXPLAIN QUERY PLAN SELECT id FROM conversations WHERE org_id=1 AND user_id=1")
            ).fetchall()
        )
    assert "ix_conversations_org_user" in plan
    assert "SCAN" not in plan


def test_every_declared_hot_index_exists(eng):
    with eng.begin() as c:
        names = {
            r[0]
            for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()
        }
    missing = set(m._HOT_INDEXES) - names
    assert not missing, f"isolation indexes missing after create_tables: {missing}"


def test_database_file_is_private(tmp_path):
    """The db holds the whole org's data; it must not be group/other readable (secrets.env is 0600)."""
    p = tmp_path / "priv.db"
    m.create_tables(m.get_engine(p))
    mode = stat.S_IMODE(p.stat().st_mode)
    assert not (mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH)), oct(mode)


def test_fk_violation_report_is_safe_on_a_clean_db(eng):
    # Diagnostics only: it must never raise, even though FK enforcement is off.
    m._log_fk_violations(eng)  # no exception = pass


def test_wal_lets_a_writer_commit_while_a_reader_holds_an_open_read_transaction(tmp_path):
    """WAL's whole point: readers and the writer do not block each other (the app runs background
    threads against one file alongside request handlers). Prove it behaviourally, not by reading the
    pragma back. A reader holds an open read transaction while a separate writer inserts and commits.
    Under WAL the writer commits against its own log; under the old rollback journal that commit waits
    on the reader's shared lock and raises "database is locked" within busy_timeout - bounded, so a
    regression to journal_mode=delete fails fast here instead of hanging CI. Driven through raw
    connections to the file our engine produced; journal_mode is persistent in the file, so this asserts
    the engine's real on-disk output, not a value we set in the same breath."""
    dbfile = tmp_path / "wal.db"
    m.create_tables(m.get_engine(dbfile))

    reader = sqlite3.connect(str(dbfile), timeout=1.0)
    reader.isolation_level = (
        None  # manual transaction control: our explicit BEGIN holds the read lock
    )
    reader.execute("BEGIN")
    reader.execute(
        "SELECT count(*) FROM organizations"
    ).fetchall()  # acquire + hold a read snapshot

    writer = sqlite3.connect(str(dbfile), timeout=1.0)
    writer.isolation_level = None
    writer.execute(
        "PRAGMA busy_timeout=500"
    )  # a rollback-journal regression raises here, never hangs
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO organizations (name, slug) VALUES ('W', 'w')")
        writer.execute("COMMIT")  # under a rollback journal: OperationalError("database is locked")
    finally:
        writer.close()
        reader.execute("ROLLBACK")
        reader.close()

    with m.get_engine(dbfile).begin() as c:
        assert c.execute(text("SELECT count(*) FROM organizations WHERE slug='w'")).scalar() == 1


def test_db_and_wal_sidecars_are_private_through_the_no_arg_engine_path(tmp_path, monkeypatch):
    """The 0600 test above passes an explicit path; the packaged app never does. It sets $ANTHILL_DB
    (desktop.configure_env -> _apply_paths), db.DB_PATH is bound from it, and startup calls get_engine()
    and create_tables() with NO explicit path (app._db). Exercise that real no-arg path, and cover the
    WAL sidecars: the -wal file holds committed-but-not-checkpointed pages (real content) and -shm its
    index, and SQLite creates both world-readable (0644, the mode the db had when the connection first
    opened it) - so they do not inherit the db's 0600 and must be locked down explicitly. All three
    files must be private to the owner."""
    dbfile = tmp_path / "anthill.db"
    monkeypatch.setattr(m, "DB_PATH", dbfile)

    eng = m.get_engine()  # no argument -> reads DB_PATH, the value $ANTHILL_DB binds at startup
    assert Path(eng.url.database) == dbfile
    m.create_tables(eng)  # locks the db and the sidecars create_all's write produced
    with eng.begin() as c:
        c.execute(
            text("INSERT INTO organizations (name, slug) VALUES ('A', 'a')")
        )  # give -wal content
    keep = eng.connect()  # hold a connection open so the -wal/-shm sidecars persist for the check
    keep.execute(text("SELECT 1"))
    try:
        assert Path(f"{dbfile}-wal").exists(), "no -wal sidecar: journal_mode is not actually WAL"
        for suffix in ("", "-wal", "-shm"):
            f = Path(f"{dbfile}{suffix}")
            if not f.exists():
                continue
            mode = stat.S_IMODE(f.stat().st_mode)
            assert not (mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH)), (
                f"{f.name} is group/other accessible: {oct(mode)}"
            )
    finally:
        keep.close()


def test_wal_sidecars_are_private_from_birth_even_without_the_reactive_chmod(tmp_path, monkeypatch):
    """The reactive chmod in _secure_db_file only secures files that already exist when it runs; a sidecar
    created after it would be a window. That window is closed proactively: get_engine pre-creates the db
    0600 before SQLite opens it, and SQLite copies the db file's mode onto the -wal/-shm it creates, so the
    sidecars are private from birth. Prove that layer stands alone - neuter the reactive chmod entirely and
    the sidecars must still come out 0600."""
    monkeypatch.setattr(m, "_secure_db_file", lambda engine: None)  # disable the reactive pass
    dbfile = tmp_path / "anthill.db"

    eng = m.get_engine(dbfile)  # pre-creates the db 0600 before SQLite opens it
    m.create_tables(eng)  # create_all's DDL is the first write -> creates the sidecars
    with eng.begin() as c:
        c.execute(text("INSERT INTO organizations (name, slug) VALUES ('A', 'a')"))
    keep = eng.connect()
    keep.execute(text("SELECT 1"))
    try:
        assert Path(f"{dbfile}-wal").exists(), "no -wal sidecar: journal_mode is not actually WAL"
        for suffix in ("", "-wal", "-shm"):
            f = Path(f"{dbfile}{suffix}")
            if not f.exists():
                continue
            mode = stat.S_IMODE(f.stat().st_mode)
            assert not (mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH)), (
                f"{f.name} born world-accessible without the reactive chmod: {oct(mode)}"
            )
    finally:
        keep.close()
