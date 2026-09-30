"""Version-2 migration: drop the orphaned org_settings.wiki_auto_promote column.

#683 removed this dead toggle from the OrgSettings model, but ensure_columns() only ever ADDS
columns - it has no way to react to one being removed. Any database created before #683 still
physically carries this column as NOT NULL with no server-side default, so the ORM's INSERT INTO
org_settings (which no longer lists the column at all, since the model doesn't declare it) hits a
NOT NULL constraint violation - i.e. every new signup 500s on such a database. Reproduced live
against a real, long-lived pre-#683 database; this simulates that same pre-#683 shape on a fresh
test db by re-adding the column by hand, exactly as an old real database would still have it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import migrate
from anthill.web.db import Organization, OrgSettings


def _engine_with_orphaned_column(tmp_path):
    """A fresh db (current schema) with the pre-#683 wiki_auto_promote column added back by hand -
    matching exactly what a real database that predates #683 still looks like today. No DEFAULT
    clause: the real column (confirmed against an actual pre-#683 database) has none - it was part
    of the very first CREATE TABLE, and the ORM's Python-side ``default=False`` was never written
    into the DDL as a SQL-level DEFAULT (that needs ``server_default=``, which this column never
    used). A DEFAULT clause here would let SQLite silently fill the omitted column and mask the bug."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mig2.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE org_settings ADD COLUMN wiki_auto_promote BOOLEAN NOT NULL"))
    return eng


def test_new_signup_row_500s_on_a_pre_683_database(tmp_path):
    """Reproduces the real bug: an OrgSettings insert that never mentions wiki_auto_promote (because
    the current model doesn't declare it) still hits the live NOT NULL column on an old database."""
    eng = _engine_with_orphaned_column(tmp_path)
    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="PreExisting", slug="preexisting")
        s.add(org)
        s.flush()
        s.add(OrgSettings(org_id=org.id, deployment_topology="solo", account_type_chosen=False))
        with pytest.raises(IntegrityError, match="wiki_auto_promote"):
            s.commit()


def test_migration_drops_the_column_and_the_same_insert_then_succeeds(tmp_path):
    eng = _engine_with_orphaned_column(tmp_path)

    with eng.begin() as conn:
        migrate._mig_0002_drop_wiki_auto_promote(conn)

    cols = {c["name"] for c in inspect(eng).get_columns("org_settings")}
    assert "wiki_auto_promote" not in cols

    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="AfterFix", slug="afterfix")
        s.add(org)
        s.flush()
        s.add(OrgSettings(org_id=org.id, deployment_topology="solo", account_type_chosen=False))
        s.commit()  # no longer raises


def test_migration_is_a_no_op_on_a_database_that_never_had_the_column(tmp_path):
    """A database created after #683 (or already migrated) never had/has this column - the
    migration must not error just because there is nothing to drop."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'clean.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    with eng.begin() as conn:
        migrate._mig_0002_drop_wiki_auto_promote(conn)  # must not raise


def test_end_to_end_run_migrations_fixes_a_pre_683_database(tmp_path):
    """The full run_migrations() path (as create_tables() calls it on every boot), not just the
    migration function in isolation - proves the fix actually applies automatically on startup."""
    eng = _engine_with_orphaned_column(tmp_path)
    with eng.begin() as conn:
        migrate._set_user_version(conn, 1)  # already past version 1, behind on version 2

    applied = migrate.run_migrations(eng, fresh=False)
    assert 2 in applied

    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="Boot", slug="boot")
        s.add(org)
        s.flush()
        s.add(OrgSettings(org_id=org.id, deployment_topology="solo", account_type_chosen=False))
        s.commit()  # no longer raises
