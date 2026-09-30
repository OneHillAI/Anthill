"""Wire the eval harness into the model picker (issue #276): an admin can benchmark an installed model
against the current one on the org's gold answers; it runs in the background and the page shows the
result."""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [
            User(org_id=o.id, email="a@a.com", role="admin", active=True),
            OrgSettings(org_id=o.id, ollama_model="qwen3:8b"),
        ]
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    monkeypatch.setattr(app_mod, "_spawn", lambda t, *a, **k: t(*a, **k))  # run the eval inline
    return c, app_mod, o.id


def _state(app_mod, org_id):
    """The persisted benchmark state for an org (mirrors what the page + status route read)."""
    s = app_mod._SessionFactory()
    try:
        cfg = s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        return app_mod._get_benchmark_state(cfg)
    finally:
        s.close()


def _gold3():
    return [SimpleNamespace(instruction=f"q{i}", output=f"a{i}") for i in range(3)]


class _FakeEval:
    winner = "cand:12b"

    def summary(self):
        return "qwen3:8b: 0.80 vs cand:12b: 0.85 -> winner: cand:12b"


def _installed(*tags):
    return lambda self: list(tags)


def test_benchmark_runs_and_stores_result(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.training.executor._gold", lambda db, oid, cfg=None: _gold3())
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models",
        _installed("qwen3:8b", "cand:12b"),
    )
    monkeypatch.setattr("anthill.lifecycle.evaluate.evaluate_models", lambda *a, **k: _FakeEval())
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    r = c.post("/models/benchmark", data={"model_tag": "cand:12b"}, follow_redirects=False)
    assert "benchmarking=cand" in r.headers["location"]  # urlencoded candidate tag
    st = _state(app_mod, org_id)
    assert (
        st["running"] is False
        and st["winner"] == "cand:12b"
        and "winner: cand:12b" in st["summary"]
    )


def test_benchmark_refused_without_gold(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.training.executor._gold", lambda db, oid, cfg=None: [])
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models",
        _installed("qwen3:8b", "cand:12b"),
    )
    c, _, _ = _admin(tmp_path, monkeypatch)
    r = c.post("/models/benchmark", data={"model_tag": "cand:12b"}, follow_redirects=False)
    assert "error=no_gold" in r.headers["location"]


def test_benchmark_refused_for_the_current_model(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.training.executor._gold", lambda db, oid, cfg=None: _gold3())
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models", _installed("qwen3:8b")
    )
    c, _, _ = _admin(tmp_path, monkeypatch)
    r = c.post("/models/benchmark", data={"model_tag": "qwen3:8b"}, follow_redirects=False)
    assert (
        "error=benchmark" in r.headers["location"]
    )  # can't benchmark the current model against itself


def test_benchmark_refused_while_one_running(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.training.executor._gold", lambda db, oid, cfg=None: _gold3())
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models",
        _installed("qwen3:8b", "cand:12b"),
    )
    # If evaluate_models is called, the guard failed to stop the second run.
    monkeypatch.setattr(
        "anthill.lifecycle.evaluate.evaluate_models",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run a second benchmark")),
    )
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    app_mod._set_benchmark_state(org_id, {"running": True, "candidate": "prior:7b"})
    r = c.post("/models/benchmark", data={"model_tag": "cand:12b"}, follow_redirects=False)
    assert (
        "benchmarking=prior" in r.headers["location"]
    )  # points at the in-flight run, not a new one
    assert _state(app_mod, org_id)["candidate"] == "prior:7b"  # state untouched


def test_status_route_reports_running(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    app_mod._set_benchmark_state(org_id, {"running": True, "candidate": "x"})
    assert c.get("/models/benchmark-status").json()["running"] is True


def test_page_shows_benchmark_button_with_gold(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.training.executor._gold", lambda db, oid, cfg=None: _gold3())

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "cand:12b", "size": 8e9, "modified_at": "2026-07-01"}]}

    monkeypatch.setattr("anthill.web.app.httpx.get", lambda url, **k: _Resp())
    c, _, _ = _admin(tmp_path, monkeypatch)
    assert 'action="/models/benchmark"' in c.get("/models").text  # button shows when gold exists
