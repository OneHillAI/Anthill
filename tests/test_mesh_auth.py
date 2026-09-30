"""The distributed mesh (orchestrator hub + node agents) is authenticated by a shared secret, and the
peer cache-fetch id can no longer inject into the LanceDB filter.

Security review: node registration, wiki promotion, and the central cache had no auth - any reachable
client could register a node and then promote content every agent grounds on (a supply-chain hole).
``ANTHILL_MESH_TOKEN`` now gates those endpoints when set. And ``/cache/fetch/{id}`` interpolated the id
straight into a LanceDB ``where`` string, so ``x' OR '1'='1`` leaked every row; the id is now validated
to sha256-hex before it is ever used in a filter.
"""

from fastapi.testclient import TestClient

from anthill import mesh_auth
from anthill.node_agent import app as node_mod
from anthill.orchestrator import app as orch_mod

_REG = {"node_id": "n1", "base_url": "http://n1:9000", "model": "m"}


def test_orchestrator_locked_by_default_when_no_token(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    c = TestClient(orch_mod.app)
    assert c.post("/nodes/register", json=_REG).status_code == 401  # secure by default: closed


def test_orchestrator_open_only_with_explicit_insecure_optout(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHILL_MESH_ALLOW_INSECURE", "1")
    c = TestClient(orch_mod.app)
    assert (
        c.post("/nodes/register", json=_REG).status_code == 201
    )  # opt-in open for single-node/dev


def test_orchestrator_endpoint_rejects_missing_token_when_configured(monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "s3cret")
    c = TestClient(orch_mod.app)
    assert c.post("/nodes/register", json=_REG).status_code == 401  # no header
    assert (
        c.post("/nodes/register", json=_REG, headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_orchestrator_endpoint_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "s3cret")
    c = TestClient(orch_mod.app)
    r = c.post("/nodes/register", json=_REG, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 201 and r.json()["registered"] == "n1"


def test_mesh_headers_present_only_when_token_set(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    assert mesh_auth.mesh_headers() == {}  # clients send nothing extra when unconfigured
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "abc")
    assert mesh_auth.mesh_headers() == {"Authorization": "Bearer abc"}


class _Chain:
    """Records every filter string handed to LanceDB's .where()."""

    def __init__(self, sink):
        self.sink = sink

    def search(self, _):
        return self

    def where(self, f):
        self.sink.append(f)
        return self

    def to_list(self):
        return []


class _FakeStore:
    def __init__(self):
        self.filters: list[str] = []
        self._table = _Chain(self.filters)


def test_cache_fetch_rejects_injection_id_before_touching_the_store(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)  # isolate the id validation from auth
    monkeypatch.setenv("ANTHILL_MESH_ALLOW_INSECURE", "1")
    fake = _FakeStore()
    monkeypatch.setattr(node_mod, "_cache_store", fake)
    c = TestClient(node_mod.app)

    r = c.get("/cache/fetch/x' OR '1'='1")
    assert r.status_code == 404  # rejected
    assert fake.filters == []  # ...and the malicious id NEVER reached the LanceDB filter


def test_cache_fetch_lets_a_valid_hex_id_through_to_the_store(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHILL_MESH_ALLOW_INSECURE", "1")
    fake = _FakeStore()
    monkeypatch.setattr(node_mod, "_cache_store", fake)
    c = TestClient(node_mod.app)

    r = c.get("/cache/fetch/deadbeefcafe0123")  # 16 hex chars = a real cache id shape
    assert r.status_code == 404  # absent from the (empty) fake store
    assert fake.filters == ["id = 'deadbeefcafe0123'"]  # ...but it did reach the query, unaltered
