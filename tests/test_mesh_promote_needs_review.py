"""A page promoted by a node is a proposal: the hub has no review queue, so it refuses unless an operator opts in,
and then only adds a new, unchecked page. Model-free."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from anthill.orchestrator import app as orch_mod
from anthill.wiki import items

PAGE = "# Q4 plan\n\nThe launch moves to March.\n"
BODY = {"slug": "q4-plan", "content": PAGE, "promoted_by": "n1", "reason": "shared"}
AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_TOKEN", "s3cret")
    monkeypatch.delenv("ANTHILL_MESH_PROMOTE_DIRECT", raising=False)
    monkeypatch.setattr(orch_mod, "ORG_WIKI_PATH", tmp_path / "org-wiki")
    monkeypatch.setattr(orch_mod, "_org_wiki", None)
    orch_mod.registry.register("n1", "http://n1:9000", "m")
    client = TestClient(orch_mod.app)
    yield client, orch_mod._wiki()


def _tree(ws):
    return {
        str(p.relative_to(ws.root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ws.root.rglob("*"))
        if p.is_file()
    }


def test_by_default_a_registered_node_cannot_put_a_page_in_the_wiki(hub):
    client, ws = hub
    before = _tree(ws)
    res = client.post("/wiki/promote", json=BODY, headers=AUTH)
    assert res.status_code == 403 and "no review queue" in res.json()["detail"]
    assert _tree(ws) == before and not (ws.wiki / "q4-plan.md").exists()


def test_without_the_mesh_token_nothing_is_accepted_either_way(hub, monkeypatch):
    client, ws = hub
    assert client.post("/wiki/promote", json=BODY).status_code == 401
    monkeypatch.setenv("ANTHILL_MESH_PROMOTE_DIRECT", "1")
    assert client.post("/wiki/promote", json=BODY).status_code == 401
    assert not (ws.wiki / "q4-plan.md").exists()


def test_when_an_operator_opts_in_a_new_page_is_added_marked_and_unchecked(hub, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_PROMOTE_DIRECT", "1")
    client, ws = hub
    res = client.post("/wiki/promote", json=BODY, headers=AUTH)
    assert res.status_code == 201 and res.json()["review"] == "unchecked"
    text = (ws.wiki / "q4-plan.md").read_text()
    assert "launch moves to March" in text and "model=node:n1" in text
    rec = items.read_page_record(text)
    assert rec.origin == "model-written" and rec.review_state == "unchecked"
    assert "promote | q4-plan from n1" in (ws.root / "log.md").read_text()


def test_an_existing_page_is_never_replaced(hub, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_PROMOTE_DIRECT", "1")
    client, ws = hub
    ws.write_page("q4-plan", "# Q4 plan\n\nThe launch is in January.\n")
    before = _tree(ws)
    res = client.post(
        "/wiki/promote",
        json=BODY | {"content": "# Q4 plan\n\nEverything is cancelled.\n"},
        headers=AUTH,
    )
    assert res.status_code == 409 and _tree(ws) == before


def test_an_unregistered_node_is_refused_even_when_opted_in(hub, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_PROMOTE_DIRECT", "1")
    client, ws = hub
    res = client.post("/wiki/promote", json=BODY | {"promoted_by": "stranger"}, headers=AUTH)
    assert res.status_code == 403 and not (ws.wiki / "q4-plan.md").exists()


def test_a_slug_cannot_leave_the_wiki(hub, monkeypatch):
    monkeypatch.setenv("ANTHILL_MESH_PROMOTE_DIRECT", "1")
    client, ws = hub
    res = client.post("/wiki/promote", json=BODY | {"slug": "../../escape"}, headers=AUTH)
    assert res.status_code == 201
    written = [p for p in ws.root.parent.rglob("*.md") if p.is_file()]
    assert all(ws.wiki in p.parents or p.parent == ws.root for p in written)
