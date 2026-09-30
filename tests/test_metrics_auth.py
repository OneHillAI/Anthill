"""The internal /api/metrics ingest endpoint is gated by the shared mesh secret.

Security review: /api/metrics was unauthenticated and trusted the body's org_id, so any client that
could reach the web app could inject metrics. It now goes through require_mesh (the same ANTHILL_MESH_TOKEN
gate as the rest of the mesh): enforced when the token is set, a no-op on a single-node/dev install where
it is unset.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db

_BODY = {"org_id": 1, "node_id": "n1", "cache_hit": False, "source": "generated", "model": "m"}


def _client(tmp_path):
    import anthill.web.app as app_mod

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    from fk_seed import seed_org_and_users

    _s = app_mod._SessionFactory()
    seed_org_and_users(_s)  # org 1 for the metric rows the endpoint inserts
    _s.commit()
    _s.close()
    return TestClient(app_mod.app)


def test_metrics_locked_by_default_when_token_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    assert (
        _client(tmp_path).post("/api/metrics", json=_BODY).status_code == 401
    )  # closed by default


def test_metrics_open_with_explicit_insecure_optout(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHILL_MESH_ALLOW_INSECURE", "1")
    r = _client(tmp_path).post("/api/metrics", json=_BODY)
    assert r.status_code == 200 and r.json() == {"ok": True}  # explicit single-node/dev opt-in


def test_metrics_rejects_missing_or_wrong_token_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "s3cret")
    c = _client(tmp_path)
    assert c.post("/api/metrics", json=_BODY).status_code == 401  # no header
    assert (
        c.post("/api/metrics", json=_BODY, headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )


def test_metrics_accepts_correct_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "s3cret")
    r = _client(tmp_path).post(
        "/api/metrics", json=_BODY, headers={"Authorization": "Bearer s3cret"}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
