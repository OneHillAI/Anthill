# Spec: enforce SQLite foreign keys

Status: accepted. Lane: `pillar:platform`. Closes #634.

## What

SQLite leaves foreign-key enforcement OFF by default, so Anthill's ~70 declared foreign keys were ORM
wiring and documentation, not database-enforced constraints: nothing stopped an orphan row, and
referential integrity rested entirely on application code. This turns enforcement on
(`PRAGMA foreign_keys=ON` in the connect listener), so the database rejects a row that references a
parent which does not exist.

Note on scope: this is a data-integrity guarantee (no orphan/dangling rows), not an access-control one.
It is orthogonal to the per-user/per-tenant isolation work (query-scoping, #611); foreign keys do not
decide who may read a row.

## Why it was not a one-liner

Turning the pragma on broke 55 tests and would have broken production delete paths. Two problems had to
be handled first.

### 1. Delete paths that would dangle a child

With enforcement on, deleting a parent that still has children fails. An audit of the reverse
foreign-key graph against the ~11 delete sites found most already cascaded (team, agent, folder), but
three were incomplete:

- **conversation delete** did not touch its `chat_messages` (or the `wiki_reviews.source_conversation_id`
  provenance link). Now deletes the messages and nulls the provenance link.
- **agent delete** removed `agent_approvals` but not `agent_runs`. Now removes both.
- **team delete** reparented conversations/tasks/agents to solo and cleared wiki reviews, but missed
  `memory_items`, `snippets`, `training_examples`, `proposed_skills`. Now reparents the user-owned ones
  to solo (matching the function's "nothing a person made is destroyed" intent) and drops the transient
  auto-distilled skill suggestions (like its pending wiki reviews).

**Approach: application-level cascade, not DB `ON DELETE` rules.** DB-level `ON DELETE CASCADE` is
cleaner but, on SQLite, adding it to an existing table needs a table rebuild (a migration) for every
already-installed database. Application cascade matches the codebase's existing idiom, needs no
migration, and works on every install the moment the pragma flips. DB-level `ON DELETE` (now that the
versioned migration runner from #635 exists) is a reasonable future robustness improvement.

### 2. Existing data and test fixtures

- **Not retroactive:** SQLite does not validate existing rows when enforcement is enabled, so an install
  that accumulated orphans while it was off keeps working; only new writes are checked. No data
  migration is needed. `_log_fk_violations` (from #633) reports any pre-existing orphans at startup.
- **Fixtures:** ~55 tests built rows with a hardcoded `org_id=1`/`user_id=1` without ever creating the
  parent Organization/User. A shared `tests/fk_seed.py::seed_org_and_users` seeds those canonical
  parents; each affected test's setup calls it.

## Tests

`tests/test_fk_enforcement.py`: the pragma is on and an orphan insert is rejected by the foreign key; a
conversation with messages deletes cleanly (messages gone). The full suite runs under enforcement, so
every delete-path and fixture is exercised with foreign keys on.
