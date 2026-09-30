"""Tests for the SQLite auto-migration that backfills new columns."""

import sqlite3

from sqlalchemy import create_engine

from anthill.web.db import create_tables
from anthill.web.migrate import ensure_columns


def _old_schema_db(path):
    """Create a DB that looks like an older anthill: org_settings without cloud cols."""
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE organizations (
            id INTEGER PRIMARY KEY, name VARCHAR, slug VARCHAR, created_at DATETIME
        )""")
    con.execute("""
        CREATE TABLE org_settings (
            id INTEGER PRIMARY KEY, org_id INTEGER,
            ollama_model VARCHAR, ollama_url VARCHAR, cache_threshold VARCHAR,
            wiki_auto_promote BOOLEAN, agent_interval_secs INTEGER,
            cost_per_query_usd VARCHAR, energy_per_query_gco2 VARCHAR
        )""")
    con.execute("INSERT INTO org_settings (id, org_id, ollama_model) VALUES (1, 1, 'qwen2.5:3b')")
    con.commit()
    con.close()


def test_migration_adds_cloud_columns(tmp_path):
    db = tmp_path / "old.db"
    _old_schema_db(str(db))
    engine = create_engine(f"sqlite:///{db}")

    create_tables(engine)  # adds brand-new tables
    added = ensure_columns(engine)  # backfills missing columns

    cloud_added = [a for a in added if a.startswith("org_settings.cloud")]
    # 8 original cloud cols + 3 for the dashboard provider-connect (key_enc/status/status_detail)
    assert len(cloud_added) == 11, cloud_added

    # The pre-existing row survives and the new columns are queryable
    con = sqlite3.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(org_settings)")]
    assert "cloud_enabled" in cols
    row = con.execute("SELECT ollama_model, cloud_provider FROM org_settings WHERE id=1").fetchone()
    con.close()
    assert row[0] == "qwen2.5:3b"  # original data intact
    assert row[1] == "openrouter"  # default backfilled


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    _old_schema_db(str(db))
    engine = create_engine(f"sqlite:///{db}")
    create_tables(engine)

    first = ensure_columns(engine)
    second = ensure_columns(engine)
    assert first  # first run added columns
    assert second == []  # second run is a no-op


def test_session_schema_migrates_existing_users(tmp_path):
    path = tmp_path / "old-users.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR, slug VARCHAR)")
    con.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, org_id INTEGER, email VARCHAR, "
        "role VARCHAR, active BOOLEAN)"
    )
    con.execute("INSERT INTO organizations VALUES (1, 'A', 'a')")
    con.execute("INSERT INTO users VALUES (1, 1, 'u@a.com', 'member', 1)")
    con.execute(
        "CREATE TABLE browser_session_orders (id VARCHAR(80) PRIMARY KEY, high_water INTEGER NOT NULL)"
    )
    con.execute("INSERT INTO browser_session_orders VALUES ('device-0000000001', 7)")
    con.commit()
    con.close()

    create_tables(create_engine(f"sqlite:///{path}"))

    con = sqlite3.connect(path)
    assert con.execute("SELECT auth_version FROM users WHERE id=1").fetchone() == (0,)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "auth_sessions" in tables
    assert "browser_session_orders" in tables
    assert con.execute(
        "SELECT high_water, observed_order FROM browser_session_orders WHERE id='device-0000000001'"
    ).fetchone() == (7, 0)
    con.close()


def test_migration_noop_on_current_schema(tmp_path):
    """A DB created fresh from the current models needs no column additions."""
    db = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db}")
    create_tables(engine)
    assert ensure_columns(engine) == []
