# Tasks: 692 per-request DB session lifecycle

## Build steps

1. `anthill/web/app.py`:
   - Add `import contextvars` (stdlib).
   - Add module-level `_request_sessions: contextvars.ContextVar[list | None]` (default `None`) - a
     per-request registry of the sessions opened during that request.
   - Split the existing `_db()` init out into `_ensure_engine()` (the current one-time engine +
     `sessionmaker` setup: schema, `ensure_columns` with the pre-change snapshot, password remediation -
     unchanged) and `_new_session()` (`_ensure_engine()` then `_SessionFactory()`).
   - Rewrite `_db()`: always return an INDEPENDENT `_new_session()` (unchanged per-call semantics); inside
     a request also append it to the registry so the middleware closes it. Do NOT share one session per
     request - that detaches ORM objects across `_db()` calls (`DetachedInstanceError`).
   - Add `_DBSessionMiddleware` (pure ASGI): for `scope["type"] == "http"`, set a fresh `[]` registry,
     `await self.app(...)`, and in `finally` close every session in the registry (swallow per-session close
     errors) and reset the var. Non-http scopes pass through untouched.
   - `app.add_middleware(_DBSessionMiddleware)` after `app` is defined.
2. Tests - `tests/test_db_session_lifecycle.py` (new): a 1-connection, `pool_timeout=1` engine + a minimal
   app whose route calls `_db()` and never closes (holding a reference in a sink so GC cannot mask the
   leak):
   - with the middleware, 5 such requests all 200 and `pool.checkedout() == 0` afterwards;
   - without the middleware, the 2nd request raises `sqlalchemy.exc.TimeoutError`;
   - `_db()` outside a request returns a standalone session the caller closes;
   - a route that opens two sessions and closes one keeps the other usable (independence, no
     `DetachedInstanceError` - the regression that sank the first shared-session attempt).
3. `docs/specs/692-db-session-lifecycle.md` (this change) - the spec.
4. `changelog.d/+692-db-session-lifecycle.fixed.md`.
5. Gates: `ruff check`, `ruff format --check`, `mypy anthill`, full `pytest -n 4 --dist loadscope` all
   green - the middleware changes session lifecycle for every route, so the *whole* suite is the safety
   net, not just the new file.

## Explicitly out of scope

`Depends(get_db)` migration; pool sizing; WebSocket handlers (no websocket route uses `_db()`).
