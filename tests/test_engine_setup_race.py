"""Engine set-up must be serialised and published all at once.

The desktop sidecar starts a background thread that opens a database session (``_db()``) while
uvicorn's start-up hook does the same (``_announce_setup_token`` -> ``_new_session()``). Set-up is
slow (create tables, snapshot, migrate), and it used to publish the engine first and the session
factory last, so the second caller saw an engine, skipped set-up and called a factory that was still
``None``: ``TypeError: 'NoneType' object is not callable`` and the app never started.

Deterministic: the slow step is a patched ``ensure_columns`` that holds the first thread mid set-up;
events (no sleeps) decide when the second thread runs and when the first is released.
"""

import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from anthill.web import app as app_mod

_WAIT = 30  # generous upper bound for events that must fire; never reached when the code is right


class _WatchedLock:
    """Wraps the set-up lock and tells the test when the named thread reaches it."""

    def __init__(self, inner, watched_thread: str, reached: threading.Event):
        self._inner = inner
        self._watched = watched_thread
        self._reached = reached

    def __enter__(self):
        if threading.current_thread().name == self._watched:
            self._reached.set()
        self._inner.acquire()
        return self

    def __exit__(self, *exc):
        self._inner.release()
        return False


@pytest.fixture
def fresh_engine_state(monkeypatch, tmp_path):
    """No engine published yet, and a throwaway database instead of the real one."""
    monkeypatch.setattr(app_mod, "_engine", None)
    monkeypatch.setattr(app_mod, "_SessionFactory", None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(app_mod, "get_engine", lambda *a, **k: eng)
    yield eng
    eng.dispose()


def test_second_caller_waits_for_setup_instead_of_seeing_half_published_state(
    monkeypatch, fresh_engine_state
):
    mid_setup = threading.Event()  # first thread is inside the slow migration step
    other_settled = threading.Event()  # second thread either finished or is waiting on the lock
    results: dict[str, object] = {}

    # Only exists once the fix is in; harmless on code without a lock.
    lock = getattr(app_mod, "_ENGINE_LOCK", None)
    if lock is not None:
        monkeypatch.setattr(app_mod, "_ENGINE_LOCK", _WatchedLock(lock, "second", other_settled))

    def slow_ensure_columns(engine, *, before_change=None):
        mid_setup.set()
        # The second thread now calls _new_session() while this set-up is unfinished. Wait until it
        # has either returned (broken code) or is queued behind the lock (fixed code).
        assert other_settled.wait(_WAIT)
        return []

    monkeypatch.setattr("anthill.web.migrate.ensure_columns", slow_ensure_columns)

    def first():
        results["first"] = app_mod._db()

    def second():
        assert mid_setup.wait(_WAIT)
        try:
            results["second"] = app_mod._new_session()
        except BaseException as exc:  # recorded, asserted below
            results["second"] = exc
        finally:
            other_settled.set()

    t1 = threading.Thread(target=first, name="first")
    t2 = threading.Thread(target=second, name="second")
    t1.start()
    t2.start()
    t1.join(_WAIT)
    t2.join(_WAIT)
    assert not t1.is_alive() and not t2.is_alive()

    assert not isinstance(results["second"], BaseException), repr(results["second"])
    assert isinstance(results["first"], Session) and isinstance(results["second"], Session)
    assert app_mod._engine is fresh_engine_state
    assert app_mod._SessionFactory is not None
    results["first"].close()
    results["second"].close()


def test_failed_setup_publishes_nothing_and_next_call_retries(monkeypatch, fresh_engine_state):
    calls = {"n": 0}

    def flaky_ensure_columns(engine, *, before_change=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("migration blew up")
        return []

    monkeypatch.setattr("anthill.web.migrate.ensure_columns", flaky_ensure_columns)

    with pytest.raises(RuntimeError, match="migration blew up"):
        app_mod._new_session()
    # Neither half is published, so nothing can pick up an engine without its factory.
    assert app_mod._engine is None
    assert app_mod._SessionFactory is None

    sess = app_mod._new_session()  # the retry runs the whole set-up again, cleanly
    try:
        assert isinstance(sess, Session)
        assert calls["n"] == 2
        assert app_mod._engine is fresh_engine_state
        assert app_mod._SessionFactory is not None
    finally:
        sess.close()


def test_setup_runs_once_when_already_published(monkeypatch, fresh_engine_state):
    calls = {"n": 0}

    def counting_ensure_columns(engine, *, before_change=None):
        calls["n"] += 1
        return []

    monkeypatch.setattr("anthill.web.migrate.ensure_columns", counting_ensure_columns)
    app_mod._new_session().close()
    app_mod._new_session().close()
    app_mod._db().close()
    assert calls["n"] == 1
