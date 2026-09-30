# 692: per-request DB session lifecycle (fix QueuePool exhaustion)

## Why

`docs/specs/692-db-session-lifecycle.md` (this change) names the gap: `_db()` (`anthill/web/app.py`)
opens a new `Session` per call from a module-level `sessionmaker`, and of its ~231 call sites only ~18
close explicitly. A `Session` releases its pooled connection only on `.close()`/`.rollback()` or GC, and
the ORM identity-map cycle defeats prompt refcount collection - so under concurrent requests, held
connections pile up faster than the cyclic GC reclaims them and the default `QueuePool` (5 + 10 overflow)
exhausts. Every further DB-backed route then blocks 30s and 500s with
`sqlalchemy.exc.TimeoutError: QueuePool limit ... reached`. Reproduced with ~4 concurrent GETs while
live-testing "683-knowledge-walkthroughs"; systemic, not introduced by any one route.

**Recommendation: bind one session to each request and close it in an ASGI middleware.** This fixes all
~231 sites at once with a ~30-line diff and no route changes, versus the textbook `Depends(get_db)`
migration, which is a ~231-site mechanical change that also does not fit the many `_db()` callers that are
not request handlers (helpers, background threads, startup). Raising the pool size is a mask, not a fix,
and SQLite's single-writer backend gains little from it - not done.

## Scope finding grounded against current code (not just the report)

- `_db()` is called from **three** contexts, only one of which leaks: HTTP request handlers (the leak,
  fixed here), background threads spawned via `_spawn` (e.g. `_set_model_pulling`, `_run_benchmark` - these
  already `try/finally: db.close()`), and startup/migration (`migrate.py`, one call). The middleware only
  covers the request context; the other two keep returning a standalone session the caller closes, so
  their existing behaviour is unchanged. Confirmed by grepping `_db()` vs `.close()` across
  `anthill/web/*.py`: `app.py` 231/18, `db.py` 1/1, `migrate.py` 1/0 (startup, single short-lived call).
- Each `_db()` must stay an **independent** session (as today), NOT one shared per request: a first,
  broken attempt shared one session per request and 19 tests went red with `DetachedInstanceError` -
  a route's explicit `db.close()` closed the shared session and detached ORM objects another `_db()` call
  was still rendering. The fix therefore only registers each independent session for a guaranteed close;
  it changes nothing about per-call semantics.
- Starlette runs **sync** endpoints in a threadpool with a *copy* of the context, so sessions registered
  inside a sync route must be visible to the middleware. The registry is therefore a **plain list**, whose
  single object is shared by the copied context - verified by a test that opens a session inside the
  endpoint and asserts the middleware still closed it (`checkedout() == 0`), and by the independence
  regression test.

## What already exists and is REUSED

- `anthill/web/app.py`'s `_db()` / `_engine` / `_SessionFactory` globals and the one-time engine
  initialisation (schema + additive migrations + password-remediation) are kept exactly; only refactored so
  that engine init (`_ensure_engine`) and session creation (`_new_session`) are separable, which the
  middleware needs. No behavioural change to init.
- `app = FastAPI(...)` gains a single `app.add_middleware(_DBSessionMiddleware)` at module load, the same
  place other app-level wiring lives. No new dependency (`contextvars` is stdlib).

## Out of scope

- The `Depends(get_db)` migration; `pool_size`/`max_overflow` tuning; WebSocket handlers (the middleware
  wraps `http` scope only; no websocket route calls `_db()` today).
