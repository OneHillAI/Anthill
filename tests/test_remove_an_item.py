"""Remove a document or a page, with a way back for 30 days (#278). Model-free: no model is called, and embeddings
are a word-hash vector."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import anthill.wiki.ask as ask_mod
from anthill.cache.page_vectors import TABLE, PageVectorIndex
from anthill.lifecycle.version import tag_page
from anthill.web.db import Team, TeamMembership
from anthill.wiki import doc_index, items, removal
from anthill.wiki.doc_index import DocIndex
from anthill.wiki.removal import RemovalError
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import _Backend, embeds  # noqa: F401
from tests.test_upload_status import _login, _setup

A = "ferry-aaaaaaaaaaaa.pdf"
B = "walrus-bbbbbbbbbbbb.pdf"
TEXT_A = "The quokka sanctuary opens at dawn and the zanzibar ferry leaves at noon, tickets cost seven coins."
TEXT_B = "The cobalt walrus guards the lantern archive under the glacier, key kept in drawer nine."
QA = "When does the quokka sanctuary open and when does the zanzibar ferry leave?"


def _world(root: Path) -> Workspace:
    """Two originals with kept text and records; a page made only from A by a model (unchecked), a page made from A
    that someone approved, a page from B, and a page with no source."""
    ws = Workspace(root, scope="personal")
    ws.init()
    ws.text.mkdir(exist_ok=True)
    for name, text in ((A, TEXT_A), (B, TEXT_B)):
        (ws.raw / name).write_bytes(b"%PDF-1.4 " + name.encode())
        (ws.text / f"{name}.txt").write_text(text, encoding="utf-8")
        items.ensure_source_record(ws, ws.raw / name)
    ws.write_page(
        "ferry", tag_page("# Ferry\n\nThe ferry page.\n", "qwen2.5:3b", on="2026-11-01", source=A)
    )
    ws.write_page(
        "ferry-checked",
        tag_page(
            "# Ferry checked\n\nChecked by a person.\n", "qwen2.5:3b", on="2026-11-01", source=A
        ),
        review="approved",
    )
    ws.write_page(
        "walrus",
        tag_page("# Walrus\n\nThe walrus page.\n", "qwen2.5:3b", on="2026-11-01", source=B),
    )
    ws.write_page("notes", "# Notes\n\nA page with no source.\n")
    doc_index.sync_workspace(ws.root)
    return ws


def _tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in ("log.md", "index.md") and ".cache" not in p.parts
    }


# ── remove a page ─────────────────────────────────────────────────────────────


def test_a_page_is_moved_out_of_the_wiki_and_its_document_stays(tmp_path):
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_page(ws, "ferry")
    assert not (ws.wiki / "ferry.md").exists()
    assert (ws.root / ".trash" / rec.id / "wiki" / "ferry.md").is_file()
    assert (ws.raw / A).is_file() and (ws.text / f"{A}.txt").is_file()  # the document is untouched
    assert "ferry.md" not in (ws.root / "index.md").read_text()
    assert [r.name for r in removal.list_removed(ws)] == ["ferry"]
    assert "remove | ferry" in (ws.root / "log.md").read_text()


def test_a_page_that_is_not_there_cannot_be_removed(tmp_path):
    ws = _world(tmp_path / "user-1")
    with pytest.raises(RemovalError):
        removal.remove_page(ws, "no-such-page")
    with pytest.raises(RemovalError):
        removal.remove_page(ws, "../index")


# ── remove a document ─────────────────────────────────────────────────────────


def test_a_document_takes_its_text_its_record_and_the_pages_only_it_made(tmp_path):
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_original(ws, A)
    assert not (ws.raw / A).exists() and not (ws.text / f"{A}.txt").exists()
    assert items.read_source_record(ws, A) is None
    assert rec.pages == ["ferry"] and not (ws.wiki / "ferry.md").exists()
    assert (ws.wiki / "ferry-checked.md").is_file()  # a person approved it: it stays
    assert (ws.wiki / "walrus.md").is_file() and (
        ws.raw / B
    ).is_file()  # the other document is untouched
    assert (
        A not in DocIndex(ws.root / ".cache", ws.text).docs()
    )  # its passages are gone from the index
    assert B in DocIndex(ws.root / ".cache", ws.text).docs()


def test_the_plan_names_what_goes_and_what_stays(tmp_path):
    ws = _world(tmp_path / "user-1")
    (ws.wiki / "legacy.md").write_text(
        tag_page("# L\n\nBefore records.\n", "qwen2.5:3b", on="2026-11-01", source=A)
    )
    plan = removal.plan_original(ws, A)
    assert plan.goes == ["ferry"] and plan.stays == [
        "ferry-checked",
        "legacy",
    ]  # no record: it stays
    assert removal.plan_original(ws, B).goes == ["walrus"]
    assert removal.plan_original(ws, "nothing-cccccccccccc.pdf") == removal.Plan()


def test_an_original_that_is_not_there_cannot_be_removed(tmp_path):
    ws = _world(tmp_path / "user-1")
    for name in ("nothing-cccccccccccc.pdf", "../x", ".hidden"):
        with pytest.raises(RemovalError):
            removal.remove_original(ws, name)


# ── restore ───────────────────────────────────────────────────────────────────


def test_restoring_returns_the_workspace_byte_for_byte_and_reindexes(tmp_path):
    ws = _world(tmp_path / "user-1")
    before = _tree(ws.root)
    rec = removal.remove_original(ws, A)
    assert _tree(ws.root) != before
    removal.restore(ws, rec.id)
    assert _tree(ws.root) == before
    assert A in DocIndex(ws.root / ".cache", ws.text).docs()  # searchable again
    assert not (ws.root / ".trash" / rec.id).exists() and removal.list_removed(ws) == []


def test_restoring_refuses_when_the_name_has_been_taken_and_moves_nothing(tmp_path):
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_page(ws, "ferry")
    ws.write_page("ferry", "# Ferry\n\nA new page with the old name.\n")
    with pytest.raises(RemovalError, match="already exists"):
        removal.restore(ws, rec.id)
    assert (ws.root / ".trash" / rec.id / "wiki" / "ferry.md").is_file()
    assert "A new page" in (ws.wiki / "ferry.md").read_text()


@pytest.mark.parametrize("rid", ["../x", "not-an-id", "zzzzzzzzzzzz", ""])
def test_only_a_real_removal_id_is_accepted(tmp_path, rid):
    ws = _world(tmp_path / "user-1")
    for action in (removal.restore, removal.delete):
        with pytest.raises(RemovalError):
            action(ws, rid)


# ── delete, and the 30 days ───────────────────────────────────────────────────


def test_deleting_leaves_nothing_derived_behind(tmp_path):
    ws = _world(tmp_path / "user-1")
    vectors = PageVectorIndex(ws.root / ".cache")
    vectors.vectors(
        [("wiki/ferry.md", "ferry"), ("wiki/walrus.md", "walrus")], lambda t: _vec(t), "m"
    )
    rec = removal.remove_original(ws, A)
    removal.delete(ws, rec.id)
    assert not (ws.root / ".trash" / rec.id).exists() and removal.list_removed(ws) == []
    keys = _vector_keys(ws)
    assert "wiki/ferry.md" not in keys and "wiki/walrus.md" in keys
    assert A not in DocIndex(ws.root / ".cache", ws.text).docs()
    assert "delete | " + A in (ws.root / "log.md").read_text()
    assert (ws.raw / B).is_file()


def _vec(text):
    import numpy as np

    v = np.zeros(8, dtype=np.float32)
    v[len(text) % 8] = 1.0
    return v


def _vector_keys(ws) -> set[str]:
    import sqlite3

    con = sqlite3.connect(PageVectorIndex(ws.root / ".cache").path)
    try:
        return {r[0] for r in con.execute(f"SELECT path FROM {TABLE}")}
    finally:
        con.close()


def _age(ws, rec, days):
    path = ws.root / ".trash" / rec.id / removal.MANIFEST
    data = json.loads(path.read_text())
    data["removed_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(data))


def test_what_was_removed_more_than_30_days_ago_is_deleted_and_the_rest_is_not(tmp_path):
    ws = _world(tmp_path / "user-1")
    old, recent = removal.remove_page(ws, "ferry"), removal.remove_page(ws, "walrus")
    _age(ws, old, 31)
    _age(ws, recent, 29)
    assert removal.expire(ws) == 1
    assert [r.name for r in removal.list_removed(ws)] == ["walrus"]
    assert removal.list_removed(ws)[0].days_left() == 1


def test_a_delete_that_was_interrupted_is_finished_at_the_next_expiry(tmp_path):
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_page(ws, "ferry")
    path = ws.root / ".trash" / rec.id / removal.MANIFEST
    data = json.loads(path.read_text())
    data["deleting"] = True  # the delete started and stopped
    path.write_text(json.dumps(data))
    with pytest.raises(RemovalError):
        removal.restore(ws, rec.id)  # no way back once a delete has started
    assert removal.expire(ws) == 1 and removal.list_removed(ws) == []


def test_expiring_every_wiki_on_the_install(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "none"))
    ws = _world(tmp_path / "wikis" / "user-1")
    rec = removal.remove_page(ws, "ferry")
    _age(ws, rec, 40)
    assert removal.expire_all() == 1
    assert removal.list_removed(ws) == []


def test_a_damaged_manifest_is_left_alone(tmp_path):
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_page(ws, "ferry")
    (ws.root / ".trash" / rec.id / removal.MANIFEST).write_text("{not json")
    assert removal.list_removed(ws) == [] and removal.expire(ws) == 0
    assert (ws.root / ".trash" / rec.id / "wiki" / "ferry.md").is_file()


def test_nothing_is_called_that_needs_a_model(tmp_path, monkeypatch):
    from anthill.inference.ollama import OllamaBackend

    def never(*a, **k):
        raise AssertionError("removing called a model")

    for method in ("chat", "chat_stream", "chat_with_tools", "chat_with_confidence"):
        monkeypatch.setattr(OllamaBackend, method, never)
    ws = _world(tmp_path / "user-1")
    rec = removal.remove_original(ws, A)
    removal.restore(ws, rec.id)
    removal.delete(ws, removal.remove_page(ws, "walrus").id)


# ── a removed item is not used in an answer ───────────────────────────────────


@pytest.mark.usefixtures("embeds")
def test_a_removed_document_and_page_never_reach_a_prompt_and_come_back_with_a_restore(tmp_path):
    ws = _world(tmp_path / "user-1")
    ws.write_page("ferry-times", "# Times\n\nThe velvet armadillo repairs the copper telescope.\n")

    def prompt(question):
        backend = _Backend()
        list(ask_mod.ask_stream(ws, question, backend))
        return backend.prompts[0]

    qp = "Who repairs the velvet armadillo copper telescope?"
    assert "tickets cost seven coins" in prompt(QA) and "armadillo repairs" in prompt(qp)
    doc = removal.remove_original(ws, A)
    page = removal.remove_page(ws, "ferry-times")
    assert "tickets cost seven coins" not in prompt(QA) and "armadillo repairs" not in prompt(qp)
    removal.restore(ws, doc.id)
    removal.restore(ws, page.id)
    assert "tickets cost seven coins" in prompt(QA) and "armadillo repairs" in prompt(qp)


# ── through the web app ───────────────────────────────────────────────────────


def _personal(ids, who="owner") -> Workspace:
    uid = ids[who][0]
    return _world(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{uid}")


def _soup(client, url):
    return BeautifulSoup(client.get(url).text, "html.parser")


def test_the_confirmation_says_what_goes_what_stays_and_what_a_delete_cannot_reach(
    tmp_path, monkeypatch
):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    page = client.get(f"/wiki/remove?kind=original&name={A}&scope=personal")
    assert page.status_code == 200
    text = BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True)
    assert "ferry" in text and "ferry-checked" in text and "30 days" in text
    assert "backups" in text and "trained" in text
    assert (ws.raw / A).is_file()  # asking changed nothing
    assert client.get("/wiki/remove?kind=original&name=nothing-cccccccccccc.pdf").status_code == 404
    assert client.get("/wiki/remove?kind=page&name=../index").status_code == 404
    assert client.get("/wiki/remove?kind=folder&name=x").status_code == 404


def test_removing_restoring_and_deleting_through_the_pages(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    files = _soup(client, "/wiki?tab=files")
    assert files.find(id="wiki-removed") is None  # nothing removed: no list
    assert any(
        a["href"].startswith("/wiki/remove?kind=original") for a in files.find_all("a", href=True)
    )
    res = client.post(
        "/wiki/remove",
        data={"kind": "original", "name": A, "scope": "personal"},
        follow_redirects=False,
    )
    assert res.status_code == 303 and "tab=files" in res.headers["location"]
    assert not (ws.raw / A).exists()
    listed = _soup(client, "/wiki?tab=files&removed=1")
    box = listed.find(id="wiki-removed")
    assert box and A in box.get_text() and "30 days left" in box.get_text()
    assert "Restore" in box.get_text() and "Delete now" in box.get_text()
    rid = removal.list_removed(ws)[0].id
    assert (
        client.post(f"/wiki/removed/{rid}/restore", data={"scope": "personal"}).status_code == 200
    )
    assert (ws.raw / A).is_file()
    rec = removal.remove_page(ws, "walrus")
    assert (
        client.post(f"/wiki/removed/{rec.id}/delete", data={"scope": "personal"}).status_code == 200
    )
    assert removal.list_removed(ws) == [] and not (ws.wiki / "walrus.md").exists()


def test_a_restore_that_cannot_be_done_says_why(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    rec = removal.remove_page(ws, "ferry")
    ws.write_page("ferry", "# Ferry\n\nNew.\n")
    res = client.post(
        f"/wiki/removed/{rec.id}/restore", data={"scope": "personal"}, follow_redirects=False
    )
    assert res.status_code == 303 and "restore_error" in res.headers["location"]
    assert "already exists" in client.get(res.headers["location"]).text


def test_a_page_offers_a_remove_link_to_whoever_can_edit_it(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    link = _soup(client, "/wiki/page/ferry").find("a", string="Remove this page")
    assert link and "kind=page" in link["href"] and "name=ferry" in link["href"]
    client.post("/wiki/remove", data={"kind": "page", "name": "ferry", "scope": "personal"})
    assert client.get("/wiki/page/ferry").status_code == 404


def test_the_registry_row_of_a_removed_page_goes_too(tmp_path, monkeypatch):
    from anthill.web import knowledge_registry
    from anthill.web.db import KnowledgeItem

    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    knowledge_registry.sync_page(
        s,
        org_id=org_id,
        scope="personal",
        team_id=None,
        slug="ferry",
        title="Ferry",
        path=str(ws.wiki / "ferry.md"),
        user_id=uid,
    )
    assert s.query(KnowledgeItem).filter(KnowledgeItem.slug == "ferry").count() == 1
    _login(client, ids["owner"])
    client.post("/wiki/remove", data={"kind": "page", "name": "ferry", "scope": "personal"})
    s.expire_all()
    assert s.query(KnowledgeItem).filter(KnowledgeItem.slug == "ferry").count() == 0
    s.close()


def _team(app_mod, ids):
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["owner"][1], name="Bakers", slug="bakers", owner_id=ids["owner"][0])
    s.add(team)
    s.flush()
    for who, role in (("owner", "owner"), ("mate", "member")):
        s.add(TeamMembership(team_id=team.id, user_id=ids[who][0], role=role, status="active"))
    s.commit()
    tid = team.id
    s.close()
    return tid


def test_only_the_people_who_may_edit_a_wiki_may_remove_things_from_it(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    tid = _team(app_mod, ids)
    team_ws = _world(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"team-{tid}")
    org_ws = _world(Path(os.environ["ANTHILL_ORG_WIKI"]))
    body = {"kind": "page", "name": "ferry"}
    cases = [
        ("owner", {"scope": "team", "team_id": tid}, 303),  # the team's owner
        ("mate", {"scope": "team", "team_id": tid}, 403),  # a member who is not the owner
        ("admin", {"scope": "team", "team_id": tid}, 403),  # not in the team
        ("owner", {"scope": "org"}, 403),  # an org member
        ("admin", {"scope": "org"}, 303),  # the org admin
    ]
    for who, scope, expected in cases:
        _login(client, ids[who])
        res = client.post("/wiki/remove", data=body | scope, follow_redirects=False)
        assert res.status_code == expected, (who, scope, res.status_code)
        for ws in (team_ws, org_ws):
            if not (ws.wiki / "ferry.md").exists():
                ws.write_page("ferry", "# Ferry\n\nBack.\n")  # reset for the next case
    _login(client, ids["mate"])
    assert (
        client.post(
            "/wiki/removed/abcdef012345/delete", data={"scope": "team", "team_id": tid}
        ).status_code
        == 403
    )
    assert (
        client.get(f"/wiki/remove?kind=page&name=ferry&scope=team&team_id={tid}").status_code == 403
    )


def test_a_solo_account_has_no_organisation_wiki_to_remove_from(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(OrgSettings).first().deployment_topology = "solo"
    s.commit()
    s.close()
    _login(client, ids["admin"])
    res = client.post(
        "/wiki/remove",
        data={"kind": "page", "name": "ferry", "scope": "org"},
        follow_redirects=False,
    )
    assert res.status_code == 404


def test_a_person_who_removes_nothing_sees_nothing_new(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    before = _tree(ws.root)
    assert _soup(client, "/wiki?tab=files").find(id="wiki-removed") is None
    assert _soup(client, "/wiki").find(id="wiki-removed") is None
    assert _tree(ws.root) == before and not (ws.root / ".trash").exists()
