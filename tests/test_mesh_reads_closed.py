"""Mesh read routes and the node agent chat API are closed to callers without the token.

The orchestrator's read routes carry the same token as its write routes. The node agent answers its own
machine with no token (a local tool such as Cursor), and every other caller needs the mesh token. A node agent
refuses to listen beyond loopback without one. Model-free.
"""

import pytest
from fastapi.testclient import TestClient

from anthill import mesh_auth
from anthill.node_agent import app as node_mod
from anthill.orchestrator import app as orch_mod

TOKEN = "t0ken-for-the-mesh-tests"
READS = ["/nodes", "/cache/stats", "/wiki/pages", "/wiki/pages/anything", "/wiki/lint"]
REMOTE = {"base_url": "http://node.example.com", "client": ("203.0.113.5", 50000)}
LOCAL = {"base_url": "http://127.0.0.1:9001", "client": ("127.0.0.1", 50000)}
CHAT = {"messages": [{"role": "user", "content": "hello"}]}


@pytest.fixture
def token(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", TOKEN)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    # The orchestrator keeps its org wiki and central index on disk; point them at a scratch folder.
    monkeypatch.setattr(orch_mod, "ORG_WIKI_PATH", tmp_path / "org-wiki")
    monkeypatch.setattr(orch_mod, "CENTRAL_INDEX_PATH", tmp_path / "central-index")
    monkeypatch.setattr(orch_mod, "_org_wiki", None)
    monkeypatch.setattr(orch_mod, "_central_index", None)


def test_every_orchestrator_route_except_health_requires_the_token(token):
    c = TestClient(orch_mod.app)
    for path in READS:
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401, path
    assert c.get("/health").status_code == 200
    good = {"Authorization": f"Bearer {TOKEN}"}
    for path in ("/nodes", "/cache/stats", "/wiki/pages", "/wiki/lint"):
        assert c.get(path, headers=good).status_code == 200, path


def test_orchestrator_reads_are_locked_when_no_token_is_set(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    c = TestClient(orch_mod.app)
    for path in READS:
        assert c.get(path).status_code == 401, path


def test_the_route_list_has_no_unguarded_read_but_health():
    # A new GET route on the orchestrator must take the token or be added here on purpose.
    open_gets = []
    for route in orch_mod.app.routes:
        if "GET" not in getattr(route, "methods", set()):
            continue
        dependant = getattr(route, "dependant", None)
        deps = [d.call for d in dependant.dependencies] if dependant else []
        if mesh_auth.require_mesh not in deps and route.path not in {
            "/health",
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
        }:
            open_gets.append(route.path)
    assert open_gets == []


def test_node_chat_answers_its_own_machine_without_a_token(token, monkeypatch):
    monkeypatch.setattr(node_mod, "ask", lambda *a, **k: ("hi", [], False))
    monkeypatch.setattr(node_mod, "build_backend", lambda cfg: object())
    c = TestClient(node_mod.app, **LOCAL)
    assert c.get("/v1/models").status_code == 200
    assert c.post("/v1/chat/completions", json=CHAT).status_code == 200


def test_node_chat_refuses_other_machines_without_the_token(token, monkeypatch):
    monkeypatch.setattr(node_mod, "ask", lambda *a, **k: ("hi", [], False))
    monkeypatch.setattr(node_mod, "build_backend", lambda cfg: object())
    c = TestClient(node_mod.app, **REMOTE)
    assert c.get("/v1/models").status_code == 401
    assert c.post("/v1/chat/completions", json=CHAT).status_code == 401
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/v1/models", headers=good).status_code == 200
    assert c.post("/v1/chat/completions", json=CHAT, headers=good).status_code == 200


def test_a_forwarded_loopback_request_is_not_this_machine(token, monkeypatch):
    c = TestClient(node_mod.app, **LOCAL)
    assert c.get("/v1/models", headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 401


def test_node_chat_is_closed_to_others_when_no_token_is_set(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    assert (
        TestClient(node_mod.app, **REMOTE).post("/v1/chat/completions", json=CHAT).status_code
        == 401
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_listening_needs_no_token(host, monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    assert mesh_auth.exposure_refusal(host) == ""


def test_listening_beyond_loopback_needs_a_token_or_the_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ANTHILL_MESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHILL_MESH_ALLOW_INSECURE", raising=False)
    assert "ANTHILL_MESH_TOKEN" in mesh_auth.exposure_refusal("0.0.0.0")
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", TOKEN)
    assert mesh_auth.exposure_refusal("0.0.0.0") == ""
    monkeypatch.delenv("ANTHILL_MESH_TOKEN")
    monkeypatch.setenv("ANTHILL_MESH_ALLOW_INSECURE", "1")
    assert mesh_auth.exposure_refusal("0.0.0.0") == ""


def test_the_wiki_agent_sends_the_mesh_token(token, monkeypatch):
    from anthill.wiki import agent as agent_mod

    seen = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr(agent_mod.httpx, "get", fake_get)
    agent_mod._propose_and_flag(
        type("W", (), {"pages": lambda self: []})(), "http://orch:8080", type("R", (), {})()
    )
    assert seen["headers"] == {"Authorization": f"Bearer {TOKEN}"}
