# Spec: per-request DB session lifecycle (#692)

Status: implemented. Lane: `pillar:platform`.
Relates to: `anthill/web/app.py` (`_db()` and all ~231 call sites), `anthill/web/db.py` (`get_engine()`).

## 1. Problem

`_db()` returns a brand-new `Session()` from a module-level `sessionmaker` on every call, bound to an
engine using SQLAlchemy's default `QueuePool` (size 5, `max_overflow` 10 = 15 connections total;
`db.py::get_engine`). A `Session` returns its pooled connection to the pool only on an explicit
`.close()` / `.rollback()`, or when it is garbage-collected. Of the ~231 `_db()` call sites in `app.py`,
only ~18 close explicitly, and the ORM identity-map cycle (session references its loaded objects, which
reference the session back) defeats prompt CPython refcount collection - so the connection is held until
the periodic cyclic GC runs.

Under concurrent requests (multiple browser tabs, several polling widgets on one page, or fast
navigation) these held connections pile up faster than the GC reclaims them, and the pool exhausts. Every
further DB-backed route then blocks for `pool_timeout` (30s) and 500s:

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00
```

Reproduced with ~4 concurrent GETs to a `_db()`-backed route while live-testing PR
"683-knowledge-walkthroughs". This is a pre-existing, systemic pattern, not introduced by any one route.

## 2. Approach

Bind **one** `Session` to each HTTP request and close it when the response finishes, so a route that
forgets `db.close()` can no longer leak its pooled connection. This fixes every one of the ~231 call
sites at once, with no route signature changes.

Rejected alternative: migrating every route to a `Depends(get_db)` dependency. It is the textbook FastAPI
pattern, but it is a ~231-site mechanical change with a large blast radius, and many `_db()` callers are
not request handlers at all (helpers, background threads, startup) and cannot take a dependency - so it is
not a clean 1:1 migration. The middleware fixes the same problem with a ~30-line diff and zero call-site
churn.

Rejected non-fix: raising `pool_size` / `max_overflow`. It masks the symptom without fixing the leak, and
SQLite's single-writer file backend gains little from a larger pool. Not done.

### Design

- `_DBSessionMiddleware` (pure ASGI, not `BaseHTTPMiddleware` - the latter runs the endpoint in a separate
  task where the `ContextVar` would not be visible) puts a fresh empty list (a per-request session
  registry) into a `ContextVar` for the duration of each `http` request, and in a `finally` closes every
  session in that list and resets the var - so all connections are returned on success **and** on error.
- `_db()` returns an **independent** new session on every call, exactly as before, and (inside a request)
  appends it to the registry so the middleware closes it. Keeping the sessions independent - rather than
  sharing one per request - is essential: a route's explicit `db.close()` must not detach ORM objects that
  another `_db()` call in the same request is still rendering (that regression is a `DetachedInstanceError`
  during template rendering). The leak fix therefore only *adds* a guaranteed close; it changes nothing
  about per-call session semantics.
- Outside a request (no registry) `_db()` returns a fresh standalone session the caller owns, exactly as
  before. The registry is a plain list (a shared mutable object) so it survives Starlette running sync
  endpoints in a threadpool with a *copy* of the context: the copy shares the same list object, so
  sessions opened inside the endpoint are still seen by the middleware's close.
- An explicit `db.close()` in a route stays correct and is a harmless no-op for the middleware
  (`Session.close()` is idempotent).

## 3. Acceptance criteria

- With the middleware, a route that calls `_db()` and never closes it returns its pooled connection after
  every request: firing many such requests against a one-connection pool never exhausts it, and
  `engine.pool.checkedout() == 0` afterwards even while the sessions are still referenced.
- Without the middleware, the same never-closing route exhausts a one-connection pool on the second
  request (`sqlalchemy.exc.TimeoutError`) - i.e. the middleware is what fixes it, not GC timing.
- `_db()` outside any request returns a standalone session the caller closes (background threads /
  startup / CLI behaviour unchanged).
- Each `_db()` call is an independent session: a route that opens two and closes one keeps the other
  usable, with no `DetachedInstanceError` - so the leak fix does not change per-call session semantics.
- The full existing suite stays green (the middleware changes session lifecycle for every route).

Covered by `tests/test_db_session_lifecycle.py`.

## 4. Out of scope

- The `Depends(get_db)` migration (not needed once the leak is fixed systemically).
- Tuning `pool_size` / `max_overflow` (a mask, not a fix).
- WebSocket handlers: the middleware only wraps `http` scope; a websocket calling `_db()` gets a
  standalone session it must close - no current websocket route uses `_db()`.
