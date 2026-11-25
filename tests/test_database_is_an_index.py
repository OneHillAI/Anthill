"""The destructive rebuild test (build plan step 3.4): edit a page outside the app, leave a proposal pending, delete the database
file and the folder of indexes, run the start-up passes, and find that nothing is lost: pages, sources, versions, states, links,
the pending proposals, and the outside edit is still noticed. Model-free.

"Start-up" here is the passes the app runs when it starts and that read the files: the record upgrade, the registry backfill, the
pending proposals put back from their files, and the passage index. The scheduler and the network parts of start-up are not run."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.wiki.ask as ask_mod
from anthill.lifecycle.version import tag_page
from anthill.web import db as db_mod
from anthill.web.db import KnowledgeItem, Organization, OrgSettings, User, WikiReview
from anthill.wiki import doc_index, items, page_store, removal
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import _Backend, embeds  # noqa: F401
from tests.test_upload_status import _login, _setup

DOC = "ferry-aaaaaaaaaaaa.pdf"
DOC_TEXT = "The zinnober kestrel nests beneath the harbour crane, and the ferry leaves at noon."
QUESTION = "Where does the zinnober kestrel nest?"
PROPOSED = "# Q4 plan\n\nThe launch moves to March.\n"
PROPOSED_EDIT = "# Ferry\n\nThe ferry leaves at midnight.\n"


def _world(ids) -> Workspace:
    """A wiki with a document and the page a model made from it, a page a person edited twice (so it has versions), a page a
    person approved, a note, and a removed document."""
    ws = Workspace(
        Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{ids['owner'][0]}", scope="personal"
    )
    ws.init()
    ws.text.mkdir(exist_ok=True)
    (ws.raw / DOC).write_bytes(b"%PDF-1.4 ferry")
    (ws.text / f"{DOC}.txt").write_text(DOC_TEXT, encoding="utf-8")
    items.ensure_source_record(ws, ws.raw / DOC)
    ws.write_page(
        "ferry",
        tag_page("# Ferry\n\nThe ferry leaves at nine.\n", "m", on="2026-11-01", source=DOC),
    )
    for text in ("ten", "eleven"):
        body = (ws.wiki / "ferry.md").read_text()
        ws.write_page(
            "ferry",
            body.replace("nine", "x").replace("ten", "x").replace("x", text),
            by_person=True,
        )
    ws.write_page("approved", "# Approved\n\nA page a person approved.\n", review="approved")
    ws.write_page("notes", "# Notes\n\nA page of my own.\n", by_person=True)
    (ws.raw / "gone-bbbbbbbbbbbb.pdf").write_bytes(b"%PDF-1.4 gone")
    items.ensure_source_record(ws, ws.raw / "gone-bbbbbbbbbbbb.pdf")
    removal.remove_original(ws, "gone-bbbbbbbbbbbb.pdf")
    doc_index.sync_workspace(ws.root)
    return ws


def _tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".cache" not in p.parts and p.name not in ("index.md", ".upgraded.json")
    }


def _propose(app_mod, ids, ws, slug, text):
    from anthill.web.agent_writes import wiki_proposer

    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    try:
        wiki_proposer(s, org_id=org_id, user_id=uid, ws_path=str(ws.root), who="a task")(slug, text)
    finally:
        s.close()


def _pending(app_mod) -> list[tuple]:
    s = app_mod._SessionFactory()
    try:
        return sorted(
            (
                (r.uid, r.slug, r.target_scope, r.proposed_by, r.content, r.status)
                for r in s.query(WikiReview).filter(WikiReview.status == "pending")
            ),
            key=lambda row: row[1],
        )
    finally:
        s.close()


def _delete_the_database_file(app_mod, eng, tmp_path):
    """Delete the real database file (and its journal files) and make a new, empty one with the same accounts."""
    eng.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        (tmp_path / f"app.db{suffix}").unlink(missing_ok=True)
    assert not (tmp_path / "app.db").exists()
    new = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(new)
    app_mod._engine = new
    app_mod._SessionFactory = sessionmaker(bind=new, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add_all(
        User(org_id=org.id, email=e, role=r, active=True)
        for e, r in (
            ("o@acme.com", "member"),
            ("m@acme.com", "member"),
            ("a@acme.com", "admin"),
            ("b@acme.com", "admin"),
        )
    )
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    s.close()


def _start_up(app_mod, monkeypatch):
    """The start-up passes that read the files (see the module docstring)."""
    monkeypatch.setattr(doc_index, "ENABLED", True)
    app_mod._backfill_knowledge_registry()
    app_mod._put_back_pending_proposals()
    app_mod._upgrade_records_at_start()
    doc_index.sync_all()


@pytest.mark.usefixtures("embeds")
def test_deleting_the_database_and_the_indexes_loses_nothing(tmp_path, monkeypatch):
    app_mod, eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _world(ids)
    _propose(app_mod, ids, ws, "q4-plan", PROPOSED)  # a new page, waiting
    _propose(app_mod, ids, ws, "ferry", PROPOSED_EDIT)  # a change to a page, waiting
    _propose(app_mod, ids, ws, "decided", "# Decided\n\nApproved before the loss.\n")
    _login(client, ids["owner"])
    s = app_mod._SessionFactory()
    decided = s.query(WikiReview).filter(WikiReview.slug == "decided").one().id
    s.close()
    assert client.post(f"/wiki/review/{decided}/approve", follow_redirects=False).status_code in (
        200,
        302,
        303,
    )
    path = ws.wiki / "ferry.md"
    path.write_text(
        path.read_text().replace("eleven", "half past eight")
    )  # a person's edit outside the app

    waiting = _pending(app_mod)
    assert [w[1] for w in waiting] == ["ferry", "q4-plan"]
    assert {p.name for p in (ws.root / "proposals").glob("*.json")} == {
        f"{w[0]}.json" for w in waiting
    }
    before_tree = _tree(ws.root)
    before_items = items.list_items(ws)
    before_versions = [p.read_text() for _n, p in page_store.versions(ws, "ferry")]
    assert (
        len(before_versions) == 2
    )  # the model's page, and the first edit; the second is the page now

    _delete_the_database_file(app_mod, eng, tmp_path)
    shutil.rmtree(ws.root / ".cache", ignore_errors=True)
    assert _pending(app_mod) == []  # the database really has lost them
    s = app_mod._SessionFactory()
    assert s.query(KnowledgeItem).count() == 0
    s.close()

    _start_up(app_mod, monkeypatch)

    assert _pending(app_mod) == waiting  # the pending proposals are back, as they were
    assert _tree(ws.root) == before_tree  # every file of the wiki is as it was
    after = items.list_items(ws)
    assert after.pages == before_items.pages and after.sources == before_items.sources
    assert after.quarantined == before_items.quarantined == []
    assert [p.read_text() for _n, p in page_store.versions(ws, "ferry")] == before_versions
    s = app_mod._SessionFactory()
    registered = {k.slug for k in s.query(KnowledgeItem).filter(KnowledgeItem.kind == "page")}
    s.close()
    assert {
        "ferry",
        "approved",
        "notes",
        "decided",
    } <= registered  # the registry is made again from the pages
    # what was indexed is indexed again, and an answer reads the document
    backend = _Backend()
    ask_mod.ask(ws, QUESTION, backend)
    assert "zinnober kestrel nests" in backend.prompts[0]
    # the outside edit is still noticed and not overwritten
    with pytest.raises(page_store.StaleSave) as err:
        ws.write_page("ferry", "# Ferry\n\nA model's version.\n")
    assert err.value.outside is True
    assert "half past eight" in (ws.wiki / "ferry.md").read_text()


@pytest.mark.usefixtures("embeds")
def test_a_proposal_put_back_from_its_file_can_still_be_approved(tmp_path, monkeypatch):
    app_mod, eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _world(ids)
    _propose(app_mod, ids, ws, "q4-plan", PROPOSED)
    _delete_the_database_file(app_mod, eng, tmp_path)
    _start_up(app_mod, monkeypatch)
    assert len(_pending(app_mod)) == 1
    s = app_mod._SessionFactory()
    rid = s.query(WikiReview).one().id
    s.close()
    _login(client, ids["owner"])
    assert client.post(f"/wiki/review/{rid}/approve", follow_redirects=False).status_code in (
        200,
        302,
        303,
    )
    assert "launch moves to March" in (ws.wiki / "q4-plan.md").read_text()
    assert _pending(app_mod) == [] and not list(
        (ws.root / "proposals").glob("*.json")
    )  # decided: no file


def test_a_decided_or_rolled_back_proposal_leaves_no_file(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _world(ids)
    _propose(app_mod, ids, ws, "q4-plan", PROPOSED)
    (waiting,) = _pending(app_mod)
    assert (ws.root / "proposals" / f"{waiting[0]}.json").is_file()
    s = app_mod._SessionFactory()
    s.add(
        WikiReview(
            org_id=ids["owner"][1],
            proposed_by=ids["owner"][0],
            slug="never",
            content="# Never\n",
            target_scope="personal",
            status="pending",
        )
    )
    s.flush()
    s.rollback()  # the transaction did not commit
    s.close()
    assert {p.stem for p in (ws.root / "proposals").glob("*.json")} == {waiting[0]}
    _login(client, ids["owner"])
    s = app_mod._SessionFactory()
    rid = s.query(WikiReview).one().id
    s.close()
    assert client.post(f"/wiki/review/{rid}/reject", follow_redirects=False).status_code in (
        200,
        302,
        303,
    )
    assert not list((ws.root / "proposals").glob("*.json"))


def test_a_file_that_cannot_be_read_or_names_a_missing_account_is_not_put_back_wrongly(
    tmp_path, monkeypatch
):
    app_mod, eng, _client, ids = _setup(tmp_path, monkeypatch)
    ws = _world(ids)
    _propose(app_mod, ids, ws, "q4-plan", PROPOSED)
    folder = ws.root / "proposals"
    (folder / ("0" * 32 + ".json")).write_text("not json")
    (folder / ("1" * 32 + ".json")).write_text(json.dumps({"uid": "1" * 32}))
    (folder / "notes.txt").write_text("not a proposal")
    _delete_the_database_file(app_mod, eng, tmp_path)
    _start_up(app_mod, monkeypatch)
    assert [w[1] for w in _pending(app_mod)] == ["q4-plan"]
    # a proposer whose account is gone: a personal proposal belongs to the owner of that wiki
    s = app_mod._SessionFactory()
    s.query(WikiReview).delete()
    s.commit()
    s.close()
    for p in folder.glob("*.json"):
        d = json.loads(p.read_text()) if p.read_text().startswith("{") else {}
        if d.get("slug") == "q4-plan":
            d["proposed_by"] = 999
            p.write_text(json.dumps(d))
    _start_up(app_mod, monkeypatch)
    assert [w[3] for w in _pending(app_mod)] == [ids["owner"][0]]
