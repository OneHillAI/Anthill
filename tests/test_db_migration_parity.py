"""Every model column can be added to an existing SQLite database by the startup migration.

Startup runs `create_tables()` and then `migrate.ensure_columns()`, which derive `ALTER TABLE ... ADD COLUMN`
from the ORM models. New tests that call `create_tables()` on a fresh file always pass, but an existing
user's database only gets the ALTER, and SQLite refuses some of them (for example a NOT NULL column with no
usable default). This builds the "older install" the other way round: take a fresh database, drop every
column SQLite lets us drop, run startup again, and require every column back.

Columns SQLite cannot drop (primary keys, indexed or foreign-keyed columns) are not covered; a floor on the
number that were covered keeps the test from quietly becoming vacuous.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine

from anthill.web.db import Base, create_tables
from anthill.web.migrate import ensure_columns

pytestmark = pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35), reason="ALTER TABLE DROP COLUMN needs SQLite 3.35"
)

MIN_COLUMNS_EXERCISED = 250


def _startup(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        create_tables(engine)
        ensure_columns(engine)
    finally:
        engine.dispose()


def _columns(con: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {
        t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        for t in tables
        if not t.startswith("sqlite_")
    }


def test_startup_restores_every_droppable_column(tmp_path):
    db = tmp_path / "anthill.db"
    _startup(db)
    con = sqlite3.connect(db)
    full = _columns(con)
    assert set(full) >= set(Base.metadata.tables), "fresh startup did not create every model table"

    dropped = []
    for table, cols in full.items():
        for col in sorted(cols):
            try:
                con.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                dropped.append((table, col))
            except sqlite3.OperationalError:
                pass  # primary key, indexed, or referenced by a constraint
    con.commit()
    con.close()
    assert len(dropped) >= MIN_COLUMNS_EXERCISED, f"only {len(dropped)} columns could be dropped"

    _startup(db)  # must not raise on an install that is missing columns

    con = sqlite3.connect(db)
    try:
        after = _columns(con)
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        con.close()
    missing = [f"{t}.{c}" for t, c in dropped if c not in after.get(t, set())]
    assert not missing, f"startup migration did not restore: {missing}"
