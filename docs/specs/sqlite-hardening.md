# Spec: SQLite hardening (WAL, isolation indexes, private db file)

Status: accepted. Lane: `pillar:privacy`.

Three low-risk database improvements from the database-architecture review, plus one deliberate
non-change recorded here so it is not mistaken for an oversight.

## 1. WAL journal

The app runs background threads (scheduler, provisioning, agent runs) against one SQLite file with
`check_same_thread=False`. SQLite's default rollback journal makes a writer block readers. Set
`PRAGMA journal_mode=WAL` on every connect (via an `Engine` `connect` listener, so it applies to every
engine including the ones tests build). `journal_mode` is persistent in the file, so this is idempotent.

## 2. Isolation-filter indexes

After the owner-scoping work (#611) essentially every read filters by `org_id` and/or `user_id` - the
isolation filter. SQLite does **not** index a foreign key automatically, so each of those was a full
table scan. Measured on 50,000 conversations: `SEARCH ... USING INDEX` at 0.08 ms vs `SCAN` at 1.40 ms,
a 17x difference that grows linearly with row count.

Added composite indexes on the hot owner columns (`conversations`, `chat_messages`, `memory_items`,
`snippets`, `folders`, `scheduled_tasks`, `agents`, `audit_log`, `training_examples`,
`team_memberships`, `wiki_reviews`). A composite `(org_id, owner)` also serves an `org_id`-only lookup
through its leftmost prefix, so one index covers both query shapes. Tasks and agents key their owner as
`created_by`, not `user_id`.

Created with `CREATE INDEX IF NOT EXISTS` in a `create_tables` top-up, because `create_all` does not add
an index to a table that already exists - so a fresh install and an upgraded one both get them. (This is
the same "no migration framework" gap that already forced `_ensure_columns`; a real migration tool is
tracked separately.)

## 3. Private database file (0600)

SQLite creates the file world-readable under the usual umask (0644), while the `secrets.env` beside it is
0600. The db holds the entire org's data (chats, wiki, memory), so on a shared host any other OS user
could read it. `create_tables` now `chmod`s the file to 0600. The encrypted secret columns were safe
regardless (their key lives in the 0600 `secrets.env`), but the content should not be readable either.

**The WAL sidecars are covered too.** Enabling WAL (section 1) means writes land first in a `<db>-wal`
sidecar, with its index in `<db>-shm`, before a checkpoint folds them back into the main file. The `-wal`
therefore holds real, recently-committed content and is exactly as sensitive as the db - but SQLite
creates it with the mode the db had when the connection *first opened it* (0644, before the `chmod` ran),
so it does **not** inherit the 0600. Locking only the main file left recent chats/wiki/memory readable to
any other OS user through `-wal`, which defeats the purpose on the shared host this protects against.
This is fixed in two layers so the guarantee is airtight, not a race:

- **Proactive (the primary fix):** `get_engine` pre-creates the database file 0600 (`_precreate_private`)
  *before* SQLite opens it. SQLite copies the main db file's mode onto the `-wal`/`-shm` when it creates
  them, so if the db is already 0600 at first open, the sidecars are private **from birth** - there is no
  window in which a sidecar exists world-readable. Only ever creates a file that does not exist yet; an
  existing db keeps its mode.
- **Reactive (belt-and-braces):** `_secure_db_file` still `chmod`s the main file and both sidecars to 0600.
  This covers an existing install whose db was created 0644 before this change shipped (and its sidecars).
  A `chmod` failure is now logged rather than silently swallowed, since it is security-relevant, but still
  never blocks startup.

## Deliberate non-change: foreign keys stay unenforced (for now)

SQLite leaves `foreign_keys` OFF by default, so the ~70 declared foreign keys are ORM wiring and
documentation, not enforced constraints. Enforcing them (`PRAGMA foreign_keys=ON`) was attempted and
**reverted** because it is not a one-liner: it broke 56 tests whose fixtures create referentially-invalid
rows, and it exposed a production path (`test_deleted_org_redirects_to_login_not_500`) that deletes an org
while users still reference it - which succeeds today and would become a 500. Doing it safely needs
`ON DELETE` rules across the schema, a clean-up for already-orphaned rows, and fixture repair. Tracked as
its own issue. In the meantime `_log_fk_violations` reports any existing orphans at startup (best-effort,
never blocking) so the debt is visible.

## Tests

`tests/test_db_hardening.py`: WAL enabled, the isolation filter uses an index (planner SEARCHes, does not
SCAN), every declared index exists, the db file is not group/other readable, and the FK-violation report
is safe on a clean db.

Two behavioural tests back the claims rather than re-reading the flags:

- **WAL actually lets a reader and writer proceed concurrently.** A reader holds an open read transaction
  while a separate connection inserts and commits; under WAL the commit succeeds, under a rollback journal
  it would raise `database is locked` within a bounded `busy_timeout` (so a regression fails fast, never
  hangs CI). Driven through raw connections to the file the engine produced, since `journal_mode` is
  persistent in the file.
- **The db and both WAL sidecars are private through the real startup path.** The packaged app sets
  `$ANTHILL_DB` (`desktop.configure_env`) and boots via the no-argument `get_engine()` / `create_tables()`
  (`app._db`), not an explicit path; the test exercises that path and asserts the main file, `-wal`, and
  `-shm` are all 0600.
