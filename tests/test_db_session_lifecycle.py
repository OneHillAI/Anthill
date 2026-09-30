"""Per-request DB session lifecycle: `_DBSessionMiddleware` closes the request's session (returning its
pooled connection) even when a route never calls `db.close()`, which was the systemic QueuePool-exhaustion
cause. Uses a 1-connection, fail-fast pool so a single leaked session exhausts it immediately."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod


@pytest.fixture
def tiny_pool(tmp_path, monkeypatch):
    """Point `_db()` at a pool of exactly one connection that gives up after 1s, so a leaked session
    exhausts it on the very next request instead of only under real concurrent load."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'p.db'}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    monkeypatch.setattr(app_mod, "_engine", eng)
    monkeypatch.setattr(app_mod, "_SessionFactory", sessionmaker(bind=eng, autoflush=False))
    return eng


def _leaky_app(with_middleware: bool, sink: list) -> FastAPI:
    """A minimal app whose route calls `_db()` and never closes it - the pattern of the ~213 real routes.
    Each session is appended to `sink` so a lingering Python reference keeps it alive (as the ORM
    identity-map cycle does in production): the connection is then only returned by an explicit close."""
    a = FastAPI()
    if with_middleware:
        a.add_middleware(app_mod._DBSessionMiddleware)

    @a.get("/leak")
    def _leak():
        db = app_mod._db()
        db.execute(text("SELECT 1"))
        sink.append(db)
        return {"ok": True}

    return a


def test_middleware_returns_the_connection_even_when_the_route_never_closes(tiny_pool):
    sink: list = []
    client = TestClient(_leaky_app(with_middleware=True, sink=sink))
    for _ in range(5):
        assert client.get("/leak").status_code == 200  # would hang+500 on the 2nd without the fix
    assert len(sink) == 5  # all five sessions are still referenced (not garbage-collected) ...
    assert (
        tiny_pool.pool.checkedout() == 0
    )  # ... yet every connection was returned, by the middleware


def test_without_the_middleware_a_leaked_session_exhausts_the_pool(tiny_pool):
    sink: list = []
    client = TestClient(_leaky_app(with_middleware=False, sink=sink), raise_server_exceptions=True)
    assert client.get("/leak").status_code == 200  # checks out the one connection, never returns it
    with pytest.raises(SATimeoutError):
        client.get("/leak")  # no connection left -> QueuePool timeout: the bug, reproduced


def test_db_outside_a_request_is_standalone_and_caller_closed(tiny_pool):
    # No request context (background thread / startup / CLI): a fresh session the caller owns.
    db = app_mod._db()
    try:
        assert db.execute(text("SELECT 1")).scalar() == 1
    finally:
        db.close()
    assert tiny_pool.pool.checkedout() == 0


def test_each_db_call_is_an_independent_session_so_an_explicit_close_does_not_detach_others(
    tiny_pool,
):
    # Regression guard: `_db()` must return INDEPENDENT sessions (not one shared per request), so a
    # route that closes one session never detaches ORM objects another `_db()` call is still using (the
    # DetachedInstanceError this whole design must avoid). db2 never queries, so the 1-connection pool is
    # enough - db1 keeps the connection and stays usable after db2 is closed.
    a = FastAPI()
    a.add_middleware(app_mod._DBSessionMiddleware)

    @a.get("/two")
    def _two():
        db1 = app_mod._db()
        db1.execute(text("SELECT 1"))
        db2 = app_mod._db()
        assert db2 is not db1  # independent sessions
        db2.close()  # closing one must not affect the other
        assert db1.execute(text("SELECT 2")).scalar() == 2  # db1 still live
        return {"ok": True}

    assert TestClient(a).get("/two").status_code == 200
    assert tiny_pool.pool.checkedout() == 0  # both sessions returned by the middleware
