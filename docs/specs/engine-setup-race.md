# Spec: database set-up finishes before anyone uses it

Status: proposed
Lane: `pillar:platform`
Relates to: `anthill/web/app.py` (`_ensure_engine`, `_new_session`, `_db`), `anthill/desktop.py` (`_ensure_engine_async`).

## Problem

On a Mac that already runs its own Ollama, the desktop app does not start. The sidecar dies in its
start-up hook with `TypeError: 'NoneType' object is not callable` (from `_new_session`, reached through
`_announce_setup_token`), and the shell reports that the backend did not answer within 120 seconds.

`_ensure_engine()` set the module global `_engine` first, then ran the slow steps (create tables, the
pre-migration snapshot, `ensure_columns`, the display-name clean-up), and set `_SessionFactory` last. It
had no lock and it returned at once whenever `_engine` was set. The desktop shell starts a background
thread that calls `_db()` as soon as it finds Ollama, which is fast when Ollama is already there. If
uvicorn's start-up hook calls `_new_session()` while that thread is still in set-up, it sees an engine,
skips set-up and calls a factory that is still `None`. The same state was also left behind for good when
a set-up step raised: `_engine` stayed set, `_SessionFactory` stayed `None`, and every later call failed.
Released v1.2.0-rc.1 and v1.2.0-rc.2 contain the start-up call that makes this fatal (v1.1.1 does not).

## Requirements

- Set-up in `_ensure_engine()` SHALL run under one lock, so two threads never run it at the same time.
- `_engine` and `_SessionFactory` SHALL be published together, only after every set-up step has
  finished. A caller that arrives during set-up SHALL wait and then get a working session.
- IF a set-up step raises, THEN neither global SHALL be set, the error SHALL reach the caller, and the
  next call SHALL run the whole set-up again.
- Once both are published, `_ensure_engine()` SHALL return at once and run no set-up again.
- The only use of `_SessionFactory` in `anthill/**` is `_new_session()`, which `_db()` calls, and it
  reads the factory only after `_ensure_engine()` has returned. The names `_engine`, `_SessionFactory`
  and `_ensure_engine` stay as they are, so tests that set them keep working.
- Nothing else changes: same steps, same order, same snapshot before a migration.

## Acceptance

- `tests/test_engine_setup_race.py` holds one thread inside a slow `ensure_columns`, calls
  `_new_session()` from a second thread, and requires both to get a session. It fails on the earlier
  code with the `TypeError` above.
- The same file shows that a failed set-up leaves both globals unset and that the next call succeeds,
  and that set-up runs once across many calls.
- The whole test suite, `ruff` and `mypy` pass.
- Not covered here: a real built app on a Mac with Ollama already running (checked by hand at release).
