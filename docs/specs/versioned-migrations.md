# Spec: versioned schema migrations (lightweight in-code runner)

Status: accepted. Lane: `pillar:platform`. Implements the chosen option (B) from issue #635.

## Problem

`create_all` builds new tables but never alters existing ones, and `ensure_columns` (the additive
auto-migrator) only adds missing columns. Anything non-additive - a rename, a drop, a type change, a
data backfill, a table rebuild (which is how SQLite adds a foreign-key `ON DELETE` rule, #634) - had no
mechanism and no recorded schema version.

## Why not Alembic

Alembic discovers migrations from an on-disk `versions/` directory by file path. Anthill ships as a
single-file PyInstaller desktop app, where that directory must be bundled as data and resolved through
`sys._MEIPASS` - a packaging burden that cannot be fully verified without a real dmg build. Its heavy
features (autogenerate, branching, offline SQL) are unused on a one-file-per-install SQLite database. A
small in-code runner fits the embedded model better and adds no dependency.

## Design

- **Version counter:** SQLite's built-in `PRAGMA user_version` (a 32-bit int in the file header). No
  extra table, atomic, and it travels with the db file.
- **Registry:** `MIGRATIONS` in `anthill/web/migrate.py` - an append-only, ascending list of
  `(version, description, fn(conn))`. Rules: never renumber, reorder, or edit a shipped migration; only
  append a higher version. Additive column adds keep going through `ensure_columns`, not here.
- **Runner:** `run_migrations(engine, *, fresh, ...)`:
  - A **fresh** database (empty - `create_all` just built the latest schema) is **stamped at head
    without running anything**; each migration's end-state already exists, so re-applying it would
    fail. `create_tables` detects fresh by inspecting for tables *before* `create_all`.
  - An **existing** database runs every migration above its `user_version`, in ascending order, **each
    in its own transaction**, so a failure rolls that one back and leaves `user_version` at the last
    success (retried next launch).
  - Before the first migration runs, a **reversible snapshot** of the db is taken
    (`anthill.backup.snapshot_db`, the same hook `ensure_columns` uses).
  - A failing migration **re-raises**; `create_tables` logs it loudly (the snapshot is already taken)
    and boots on the current schema rather than bricking startup.

## Scope of this change

The runner is introduced with an **empty** `MIGRATIONS` registry, so it is a no-op stamp in production
today (zero behaviour change) and the machinery is proven by tests that inject migrations. The first
real migration lands when a non-additive change needs it (starting with #634).

## Not in this change (follow-ups)

- Consolidating the two redundant additive column-adders (`db.py::_ensure_columns` and
  `migrate.py::ensure_columns`) into one path.
- The first real migration (FK `ON DELETE` rules for #634).

## Status update: migration 2 - a real, production-hit landmine class

Migration 1 (org_council_members backfill) shipped as this system's first real use. Migration 2
(`_mig_0002_drop_wiki_auto_promote`) fixes a *different*, previously-unhandled hazard this design
didn't anticipate: **removing a `nullable=False` column from the ORM model, with no migration at
all.**

#683 deleted `OrgSettings.wiki_auto_promote` from the model as dead code ("rendered, stored, read by
nothing" - a correct call on its own). But `ensure_columns` only ever *adds* columns the model
declares and the database lacks; it has no way to react to a column disappearing from the model. Any
database created before #683 still physically carries that column, `NOT NULL` with no server-side
default (it predates `ensure_columns` entirely - it was part of the very first `CREATE TABLE`, before
this migration system existed, so its Python-side `default=False` was never written into the DDL).
Since the *current* model no longer lists the column at all, every `INSERT INTO org_settings` the ORM
issues since #683 omits it outright - and SQLite rejects the insert. Concretely: **every new signup -
first account or an additional one - 500s, forever, on any database that predates #683**, since
nothing in the existing additive-only machinery would ever notice or fix it up. Reported live in
production and reproduced against a copy of a genuinely pre-#683 database (not just a fresh test db).

Migration 2 drops the orphaned column outright (SQLite 3.35+ supports `DROP COLUMN` directly; the
column carried no index/FK/unique constraint), completing what #683 intended rather than leaving a
permanently `NOT NULL`-but-ignored zombie column in the schema.

**The general lesson, for future column removals:** deleting a `nullable=False` column from a model is
not safe by itself on a codebase whose only auto-migration path is additive. It needs either (a) a
paired versioned migration that drops (or relaxes) the column on existing databases, in the same
change that removes it from the model, or (b) making the column nullable (or giving it a
`server_default`) well before it is ever removed. A dead-code removal that only edits the ORM model is
incomplete without one of these.

## Tests

`tests/test_migration_runner.py`: fresh db stamped to head without running; existing db runs pending in
order after a snapshot; only versions above the current run; a failure rolls back and holds the version
at the last good one; the default snapshot hook fires; the production registry stays ascending + unique.

`tests/test_migration_drop_wiki_auto_promote.py`: reproduces the exact production failure (an
OrgSettings insert against a simulated pre-#683 schema raises the real `NOT NULL` `IntegrityError`);
confirms the migration drops the column and the same insert then succeeds; confirms the migration is a
no-op on a database that never had the column; confirms the fix applies automatically through the full
`run_migrations` boot path, not just the migration function in isolation.
