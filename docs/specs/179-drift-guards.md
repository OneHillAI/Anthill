# Spec: Deterministic guards for mypy debt, startup migration and doc paths

Status: implemented. Lane: `pillar:platform`. Issue: #179.

## Problem

Three kinds of drift are only caught by a person reading a diff, or by the post-merge documentation agent:

- `pyproject.toml` exempts 18 modules from mypy (`ignore_errors = true`) with the note to remove one once it is
  cleaned. Nothing stops a PR from adding a nineteenth, and a renamed module silently drops off the list.
- The startup migration adds new model columns to an existing SQLite database. Tests that build a fresh database
  never run that path, so a column it cannot add would pass CI and fail on a user's upgrade.
- AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md and docs/*.md name files that get renamed or deleted.

## Requirements

1. A test fails when a module is added to the mypy ignore list, when a module leaves the list without the
   baseline in the test being lowered, and when a listed module does not exist.
2. A test builds a fresh database with the startup path (`create_tables` then `ensure_columns`), drops every
   column SQLite allows to be dropped, runs startup again, and fails if startup raises, if any dropped column is
   not restored, or if the database fails `PRAGMA integrity_check`. It fails if fewer than 250 columns could be
   dropped, so it cannot go vacuous. It is skipped on SQLite older than 3.35.
3. A test fails when a relative markdown link, or a backticked repo path (`anthill/`, `scripts/`, `cli/`,
   `docs/`, `.github/`, `tests/`, `src-tauri/`, `tools/`, `recipes/`, `skills/`), in AGENTS.md, ARCHITECTURE.md,
   CONTRIBUTING.md, `.asdd.yml` or docs/*.md points at a file that does not exist.
4. Two references are listed as known: `docs/walkthrough.md` in a roadmap line of ARCHITECTURE.md (the file never
   existed in this repo) and `cli/README.md` in docs/asdd-goose-adoption.md (a path in the ASDD repo). The list
   fails when an entry starts to resolve, so it only shrinks.
5. docs/SYSTEM_IMPACT_LOG.md is not scanned: it is a history and rightly names files that were removed.
6. No new dependency; runs offline, model-free.

## Known limits

- The startup test covers columns SQLite can drop. Primary keys and indexed or foreign-keyed columns (124 of 543
  today) are not exercised.
- It checks that each column comes back, not its declared type, nullability or default. `ensure_columns` adds
  loose, nullable columns by design.
- The versioned migrations (`PRAGMA user_version`) are not exercised: a fresh database is stamped as current, and
  forcing them to run on a synthetic database with every column removed fails for reasons that are not real
  upgrades.
- Doc CLI subcommand names (`anthill <cmd>`) are not checked, and neither is whether the 18 modules would now
  pass mypy.

## Acceptance

- On `main` today the tests pass.
- Adding a module to the mypy list, removing one without lowering the baseline, or misspelling one fails.
- A model column `ensure_columns` skips, or one it emits invalid SQL for, fails the startup test.
- A dead link or backticked path in a scanned doc fails; fixing a known reference without removing it from the
  list fails.
- Tests: `tests/test_mypy_ratchet.py`, `tests/test_db_migration_parity.py`, `tests/test_doc_paths.py`
  (model-free).
