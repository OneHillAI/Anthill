"""Answers read only items that are active and approved (#277, slice 1b of #270). One rule, checked on every route
that reads knowledge. Model-free: the model is a recording stand-in and embeddings are a word-hash vector."""

import os
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import anthill.wiki.ask as ask_mod
from anthill.agent import tools
from anthill.cache import embedder as emb
from anthill.lifecycle.version import tag_page
from anthill.web import records_update
from anthill.wiki import doc_context, doc_index, items
from anthill.wiki.doc_index import DocIndex
from anthill.wiki.items import ItemRecord
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import _Backend, chat, embeds  # noqa: F401
from tests.test_upload_status import _login, _setup

pytestmark = pytest.mark.usefixtures("embeds")

ACTIVE = "ferry-aaaaaaaaaaaa.pdf"
HELD = "kestrel-bbbbbbbbbbbb.pdf"
REJECTED = "walrus-cccccccccccc.pdf"
UNRECORDED = "armadillo-dddddddddddd.pdf"
TEXTS = {
    ACTIVE: "The quokka sanctuary opens at dawn and the zanzibar ferry leaves at noon, tickets cost seven coins.",
    HELD: "The marmalade kestrel nests above the harbour lighthouse every winter, in room forty-two.",
    REJECTED: "The cobalt walrus guards the lantern archive under the glacier, key kept in drawer nine.",
    UNRECORDED: "The velvet armadillo repairs the copper telescope behind the orchard, on rainy mondays.",
}
Q = {
    ACTIVE: "When does the quokka sanctuary open and when does the zanzibar ferry leave?",
    HELD: "Where does the marmalade kestrel nest above the harbour lighthouse?",
    REJECTED: "Who does the cobalt walrus guard under the glacier lantern archive?",
    UNRECORDED: "What does the velvet armadillo repair behind the orchard, the copper telescope?",
}
TRASHED_PAGE = "# Ledger\n\nThe saffron heron keeps the obsidian ledger of the northern marsh, behind the grey shed.\n"
LEGACY_PAGE = (
    "# Tides\n\nThe amber otter tends the walnut ledger of the tides, under the old pier.\n"
)
Q_TRASHED = "Who keeps the saffron heron obsidian ledger of the northern marsh?"
Q_LEGACY = "Who tends the amber otter walnut ledger of the tides?"


def _set_state(ws: Workspace, name: str, review: str, lifecycle: str = "active") -> None:
    rec = items.read_source_record(ws, name)
    rec.review_state, rec.lifecycle = review, lifecycle
    items.write_source_record(ws, rec)


def _world(root: Path) -> Workspace:
    """One workspace holding an approved document with its page, a document whose upload is waiting, one that
    was rejected, a page that is trashed, a page with no record and a document with no record."""
    ws = Workspace(root, scope="personal")
    ws.init()
    ws.text.mkdir(exist_ok=True)
    for name, text in TEXTS.items():
        (ws.raw / name).write_bytes(b"%PDF-1.4 " + name.encode())
        (ws.text / f"{name}.txt").write_text(text, encoding="utf-8")
    for name in (ACTIVE, HELD, REJECTED):
        items.ensure_source_record(ws, ws.raw / name)
    ws.write_page(
        "ferry",
        tag_page("# Ferry\n\nThe ferry page.\n", "qwen2.5:3b", on="2026-11-01", source=ACTIVE),
    )
    _set_state(ws, HELD, "proposed")
    _set_state(ws, REJECTED, "rejected")
    path = ws.write_page("ledger", TRASHED_PAGE)
    path.write_text(path.read_text().replace("lifecycle: active", "lifecycle: trashed"))
    (ws.wiki / "tides.md").write_text(LEGACY_PAGE)  # written before records: no record
    doc_index.sync_workspace(ws.root)
    return ws


# ── the rule ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lifecycle,review,expected",
    [
        ("active", "checked", True),
        ("active", "unchecked", True),
        ("active", "proposed", False),
        ("active", "rejected", False),
        ("superseded", "checked", False),
        ("archived", "checked", False),
        ("trashed", "checked", False),
        ("purged", "checked", False),
    ],
)
def test_only_active_and_approved_items_are_retrievable(lifecycle, review, expected):
    rec = ItemRecord(kind="page", space="personal", lifecycle=lifecycle, review_state=review)
    assert items.is_retrievable(rec) is expected


def test_an_item_with_no_record_is_retrievable_as_before():
    assert items.is_retrievable(None) is True


def test_a_page_file_is_judged_from_its_record_and_remembered_until_it_changes(tmp_path):
    ws = _world(tmp_path / "user-1")
    ferry, ledger, tides = (ws.wiki / f"{s}.md" for s in ("ferry", "ledger", "tides"))
    assert items.page_file_retrievable(ferry) and items.page_file_retrievable(tides)
    assert not items.page_file_retrievable(ledger)
    ledger.write_text(ledger.read_text().replace("lifecycle: trashed", "lifecycle: active"))
    assert items.page_file_retrievable(ledger)  # restored: the change is seen
    broken = ws.wiki / "broken.md"
    broken.write_text(
        "---\ntype: Concept\nx-anthill-item:\n  id: a\n  kind: page\n  space: personal\n"
        "  lifecycle: gone\n---\n\n# B\n"
    )
    assert not items.page_file_retrievable(
        broken
    )  # a damaged record cannot be shown to be approved
    assert not items.page_file_retrievable(ws.wiki / "gone.md")


def test_the_hidden_originals_are_named_and_a_damaged_record_hides_its_original(tmp_path):
    ws = _world(tmp_path / "user-1")
    assert items.hidden_sources(ws.root) == {HELD, REJECTED}
    sidecar = ws.root / "records" / "sources" / f"{ACTIVE}.json"
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text("{not json")
    tmp.replace(
        sidecar
    )  # records are written by replacing the file, which the folder's timestamp shows
    assert items.hidden_sources(ws.root) == {HELD, REJECTED, ACTIVE}
    assert items.hidden_sources(tmp_path / "nothing-here") == frozenset()


# ── the document passages ─────────────────────────────────────────────────────


def test_a_held_a_rejected_and_a_failed_original_never_give_a_passage(tmp_path):
    ws = _world(tmp_path / "user-1")
    for name in (ACTIVE, UNRECORDED):
        got = doc_context.document_passages([ws], Q[name])
        assert [h.doc for h in got] == [name]
    for name in (HELD, REJECTED):
        assert doc_context.document_passages([ws], Q[name]) == []
    _set_state(ws, ACTIVE, "proposed")  # an upload that failed or timed out stays "proposed"
    assert doc_context.document_passages([ws], Q[ACTIVE]) == []


def test_a_hidden_document_never_takes_the_place_of_a_visible_one(tmp_path):
    ws = _world(tmp_path / "user-1")
    for i in range(8):  # enough other documents that the question's words stay rare
        (ws.text / f"filler{i}-{i:012x}.pdf.txt").write_text(
            f"Filler {i} about wombat{i} and tapir{i}."
        )
    # the visible document is long and the waiting one short with the same words, so the waiting one ranks first
    (ws.text / f"{ACTIVE}.txt").write_text(TEXTS[ACTIVE] + " Padding words fill the page." * 12)
    (ws.text / f"{HELD}.txt").write_text(
        "The quokka sanctuary and the zanzibar ferry, in room forty-two."
    )
    doc_index.sync_workspace(ws.root)
    index = DocIndex(ws.root / ".cache", ws.text)
    unfiltered = index.search(Q[ACTIVE], 1, min_rare=doc_context.MIN_RARE)
    assert [h.doc for h in unfiltered] == [HELD]  # without the rule it takes the place
    got = doc_context.document_passages([ws], Q[ACTIVE], limit=1)
    assert [h.doc for h in got] == [ACTIVE]


def test_more_hidden_documents_than_one_query_can_name_are_still_left_out(tmp_path, monkeypatch):
    ws = _world(tmp_path / "user-1")
    monkeypatch.setattr(doc_index, "_MAX_EXCLUDED", 1)
    hits = DocIndex(ws.root / ".cache", ws.text).search(
        Q[HELD] + " " + Q[REJECTED], 5, exclude=frozenset({HELD, REJECTED})
    )
    assert all(h.doc not in (HELD, REJECTED) for h in hits)


# ── every route that builds an answer ─────────────────────────────────────────


def _prompts(ws, question, *, stream):
    backend = _Backend()
    if stream:
        list(ask_mod.ask_stream(ws, question, backend))
    else:
        ask_mod.ask(ws, question, backend)
    return backend.prompts[0]


@pytest.mark.parametrize("stream", [True, False], ids=["ask_stream", "ask"])
def test_what_is_hidden_never_reaches_a_prompt_and_what_is_visible_does(tmp_path, stream):
    ws = _world(tmp_path / "user-1")
    assert "tickets cost seven coins" in _prompts(ws, Q[ACTIVE], stream=stream)
    assert "on rainy mondays" in _prompts(
        ws, Q[UNRECORDED], stream=stream
    )  # no record: read as before
    assert "under the old pier" in _prompts(ws, Q_LEGACY, stream=stream)  # a page with no record
    for question, secret in (
        (Q[HELD], "room forty-two"),
        (Q[REJECTED], "drawer nine"),
        (Q_TRASHED, "behind the grey shed"),
    ):
        assert secret not in _prompts(ws, question, stream=stream)


def test_a_hidden_page_never_takes_the_place_of_a_visible_one(tmp_path):
    ws = _world(tmp_path / "user-1")
    got = ask_mod._relevant_pages(ws, Q_LEGACY + " " + TRASHED_PAGE, 3)
    assert "ledger" not in [p.stem for p in got] and "tides" in [p.stem for p in got]


def test_the_keyword_fallback_leaves_out_hidden_pages(tmp_path, monkeypatch):
    ws = _world(tmp_path / "user-1")

    def down(_text):
        raise RuntimeError("the embedder is not running")

    monkeypatch.setattr(emb, "embed", down)
    got = ask_mod._relevant_pages(ws, "saffron heron obsidian ledger amber otter walnut", 5)
    assert [p.stem for p in got] == ["tides"]


def test_the_agent_tools_do_not_find_or_read_a_hidden_page(tmp_path):
    ws = _world(tmp_path / "user-1")
    found = {r["slug"] for r in tools._search_wiki("ledger", workspace=str(ws.root), k=5)}
    assert "ledger" not in found and "tides" in found
    assert "not found" in tools._read_wiki("ledger", workspace=str(ws.root))
    assert "under the old pier" in tools._read_wiki("tides", workspace=str(ws.root))


def test_a_cached_answer_built_on_a_hidden_item_is_not_served(tmp_path, monkeypatch):
    from anthill.cache import SemanticCache

    ws = _world(tmp_path / "user-1")
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.judge = ask_mod.cache_judge([ws])
    cache.store(Q[ACTIVE], "at dawn and noon", slugs=[f"{doc_context.DOC_PREFIX}{ACTIVE} (page 1)"])
    cache.store(Q[HELD], "winter", slugs=[f"{doc_context.DOC_PREFIX}{HELD} (page 1)"])
    cache.store(Q_TRASHED, "the heron", slugs=["ledger"])
    cache.store(Q_LEGACY, "the otter", slugs=["tides"])
    assert cache.lookup(Q[ACTIVE]) is not None and cache.lookup(Q_LEGACY) is not None
    assert cache.lookup(Q[HELD]) is None and cache.lookup(Q_TRASHED) is None
    assert cache._store.count() == 4  # kept for a chat where they hold, not deleted


def test_a_new_hidden_document_does_not_stale_an_answer(tmp_path, monkeypatch):
    from anthill.cache import SemanticCache

    ws = _world(tmp_path / "user-1")
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.judge = ask_mod.cache_judge([ws])
    cache.store(Q[HELD], "I do not know", slugs=[])
    os.utime(
        ws.text / f"{HELD}.txt", None
    )  # a waiting upload that could answer it, newer than the answer
    assert cache.lookup(Q[HELD]) is not None  # it is waiting: it settles nothing


def test_the_chat_route_never_gives_the_model_a_hidden_document(request):
    session = request.getfixturevalue("chat")
    ws = Workspace(Path(os.environ["ANTHILL_WIKI_ROOT"]) / "user-1", scope="personal")
    for name in (HELD, REJECTED, UNRECORDED):
        (ws.raw / name).write_bytes(b"%PDF-1.4 " + name.encode())
        (ws.text / f"{name}.txt").write_text(TEXTS[name], encoding="utf-8")
    for name in (HELD, REJECTED):
        items.ensure_source_record(ws, ws.raw / name)
    _set_state(ws, HELD, "proposed")
    _set_state(ws, REJECTED, "rejected")
    doc_index.sync_workspace(ws.root)
    session.ask(Q[HELD])
    session.ask(Q[REJECTED])
    session.ask(Q[UNRECORDED])
    held, rejected, unrecorded = session.state["prompts"][-3:]
    assert "room forty-two" not in held and "drawer nine" not in rejected
    assert "on rainy mondays" in unrecorded


# ── the states move when a decision is made ───────────────────────────────────


def _review(slug, source, org_id=1, **kw):
    from anthill.web import db as db_mod

    content = tag_page("# R\n\nBody.\n", "qwen2.5:3b", on="2026-11-01", source=source)
    return db_mod.WikiReview(org_id=org_id, slug=slug, content=content, **kw)


def test_rejecting_a_review_marks_its_original_rejected(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    uid, org_id, _role = ids["owner"]
    ws = Workspace(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{uid}", scope="personal")
    ws.init()
    (ws.raw / HELD).write_bytes(b"%PDF-1.4 held")
    items.ensure_source_record(ws, ws.raw / HELD)
    s = app_mod._SessionFactory()
    rev = _review(
        "held", HELD, org_id=org_id, proposed_by=uid, target_scope="personal", status="pending"
    )
    s.add(rev)
    s.commit()
    rid = rev.id
    s.close()
    _login(client, ids["owner"])
    res = client.post(f"/wiki/review/{rid}/reject", follow_redirects=False)
    assert res.status_code == 302
    assert items.read_source_record(ws, HELD).review_state == "rejected"
    assert HELD in items.hidden_sources(ws.root)


def test_rejecting_a_second_copy_of_a_file_does_not_hide_the_first_copys_original(tmp_path):
    ws = _world(tmp_path / "user-1")
    assert items.mark_source_rejected(ws, ACTIVE) is False  # a page made from it stands
    assert items.read_source_record(ws, ACTIVE).review_state == "checked"
    assert items.mark_source_rejected(ws, HELD) is True
    assert items.mark_source_rejected(ws, HELD) is False  # already rejected
    assert items.mark_source_rejected(ws, "no-such-eeeeeeeeeeee.pdf") is False


def test_uploading_a_rejected_file_again_makes_it_a_new_proposal(tmp_path):
    ws = _world(tmp_path / "user-1")
    items.ensure_source_record(ws, ws.raw / REJECTED)
    assert (
        items.read_source_record(ws, REJECTED).review_state == "rejected"
    )  # a lookup alone changes nothing
    items.ensure_source_record(ws, ws.raw / REJECTED, reopen=True)
    assert items.read_source_record(ws, REJECTED).review_state == "proposed"


def test_a_page_that_lands_approves_a_rejected_original(tmp_path):
    ws = _world(tmp_path / "user-1")
    ws.write_page(
        "walrus",
        tag_page("# W\n\nThe walrus page.\n", "qwen2.5:3b", on="2026-11-01", source=REJECTED),
    )
    assert items.read_source_record(ws, REJECTED).review_state == "checked"


# ── the one-time notice and its button ────────────────────────────────────────


def _old_install(tmp_path, monkeypatch):
    """An install whose personal wiki holds an original with no record, as every upload before records did."""
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    uid = ids["admin"][0]
    ws = Workspace(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{uid}", scope="personal")
    ws.init()
    (ws.raw / HELD).write_bytes(b"%PDF-1.4 held")
    ws.text.mkdir(exist_ok=True)
    (ws.text / f"{HELD}.txt").write_text(TEXTS[HELD], encoding="utf-8")
    return client, ids, ws


def _notice(client, extra=""):
    html = client.get(f"/wiki{extra}").text
    return BeautifulSoup(html, "html.parser").find(id="records-notice"), html


def _tree(root: Path) -> dict:
    import hashlib

    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".cache" not in p.parts and ".db" not in p.name
    }


def test_an_old_install_is_told_once_and_nothing_changes_until_the_button(tmp_path, monkeypatch):
    client, ids, ws = _old_install(tmp_path, monkeypatch)
    _login(client, ids["admin"])
    before = _tree(tmp_path)
    notice, _html = _notice(client)
    assert notice is not None
    assert (
        "Update records" in notice.get_text() and "anthill knowledge inventory" in notice.get_text()
    )
    assert _tree(tmp_path) == before  # looking, and starting the app, wrote nothing
    assert items.list_items(ws).unrecorded_sources == [HELD]


def test_the_button_makes_a_backup_adds_the_records_and_the_notice_is_gone(tmp_path, monkeypatch):
    client, ids, ws = _old_install(tmp_path, monkeypatch)
    _login(client, ids["admin"])
    res = client.post("/wiki/records/update", follow_redirects=False)
    assert res.status_code == 303 and "records=updated" in res.headers["location"]
    assert items.list_items(ws).unrecorded_sources == []
    assert any((ws.root.parent / "_backups").glob("user-*.tar.gz"))
    notice, html = _notice(client, "?records=updated&pages=0&originals=1")
    assert notice is None and "Records added to 0 page(s) and 1 original(s)" in html


def test_the_button_does_what_the_command_does_and_nothing_else(tmp_path, monkeypatch):
    from anthill.wiki import inventory as inv

    client, ids, ws = _old_install(tmp_path, monkeypatch)
    twin = Workspace(tmp_path / "twin" / ws.root.name, scope="personal")
    import shutil

    shutil.copytree(ws.root, twin.root)
    inv.apply_records(twin, review_status={})  # what the command does to the same wiki
    _login(client, ids["admin"])
    client.post("/wiki/records/update")
    done = {(s.name, s.review_state) for s in items.list_items(ws).sources}
    assert done == {(s.name, s.review_state) for s in items.list_items(twin).sources}
    assert done == {
        (HELD, "proposed")
    }  # a leftover with no page and no review row stays out of answers


def test_not_now_dismisses_the_notice_for_good(tmp_path, monkeypatch):
    client, ids, ws = _old_install(tmp_path, monkeypatch)
    _login(client, ids["admin"])
    assert client.post("/wiki/records/dismiss", follow_redirects=False).status_code == 303
    assert _notice(client)[0] is None
    assert items.list_items(ws).unrecorded_sources == [HELD]  # nothing was changed
    assert records_update.notice_due() is False


def test_only_the_person_who_runs_the_install_sees_or_uses_the_notice(tmp_path, monkeypatch):
    client, ids, ws = _old_install(tmp_path, monkeypatch)
    _login(client, ids["owner"])  # a member
    assert _notice(client)[0] is None
    assert client.post("/wiki/records/update").status_code == 403
    assert client.post("/wiki/records/dismiss").status_code == 403
    assert items.list_items(ws).unrecorded_sources == [HELD]


def test_an_install_with_nothing_to_update_shows_no_notice(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["admin"])
    assert _notice(client)[0] is None


def test_an_install_that_never_presses_the_button_still_answers_as_before(tmp_path, monkeypatch):
    _client, _ids, ws = _old_install(tmp_path, monkeypatch)
    doc_index.sync_workspace(ws.root)
    assert items.hidden_sources(ws.root) == frozenset()  # no records: nothing is hidden
    assert [h.doc for h in doc_context.document_passages([ws], Q[HELD])] == [
        HELD
    ]  # as before records
