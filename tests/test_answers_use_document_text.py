"""An answer can use the user's own document, not only the model's summary of it (issue #226, step 2).

The passages of the kept text that match the question go first in the answer's context, labelled as the original;
the summary page of the same document is labelled as a summary that may contain errors; nothing is added when no
passage qualifies. Model-free: the model and the embedder are fakes."""

import hashlib
import json
import re
import types

import numpy as np
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.wiki.ask as ask_mod
from anthill.cache import embedder as emb
from anthill.lifecycle.version import tag_page
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import ChatMessage, Conversation, Organization, OrgSettings, User
from anthill.wiki import doc_context, doc_index
from anthill.wiki.workspace import Workspace, workspace_for

HANDBOOK = (
    "Hollis Bakery: operations handbook\n\nHollis Bakery was founded in 2011 and employs fourteen people.\n\n"
    "---\n\nSuppliers and deliveries\n\nFlour comes from the Brandt mill, delivered every Tuesday and Friday.\n\n"
    "Butter comes from the Wiesenhof cooperative. Orders must be placed by Wednesday noon for the Thursday "
    "delivery, otherwise the price rises by eight percent.\n\nThe delivery van has the licence plate ZX-4417."
)
# What the model once wrote for it: a changed fact (Flurf for Brandt) and a dropped one (the eight percent).
SUMMARY = (
    "# Hollis Bakery: Operations Handbook\n\nHollis Bakery was founded in 2011 and employs fourteen people. "
    "It sources its flour from Flurf and its butter from the Wiesenhof cooperative.\n"
)
RAW = "hollis-bakery-handbook-1a2b3c4d5e6f.pdf"


def _vector(text: str) -> np.ndarray:
    vec = np.zeros(emb.DIM, dtype=np.float32)
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % emb.DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


@pytest.fixture
def embeds(monkeypatch):
    calls: list[str] = []

    def _embed(text: str) -> np.ndarray:
        calls.append(text)
        return _vector(text)

    getattr(emb, "_recent", {}).clear()
    monkeypatch.setattr(emb, "embed", _embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setenv("ANTHILL_DISABLE_SEMANTIC_CACHE", "1")
    monkeypatch.setattr("anthill.wiki.page_index.warm_page_soon", lambda *a, **k: None)
    monkeypatch.setattr(doc_context, "ENABLED", True)
    yield calls
    getattr(emb, "_recent", {}).clear()


def _wiki(tmp_path, name="w", *, summary=SUMMARY, text=HANDBOOK, raw=RAW) -> Workspace:
    ws = Workspace(tmp_path / name, scope="personal")
    ws.init()
    (ws.raw / raw).write_bytes(
        b"%PDF-1.4 handbook"
    )  # the original is kept; writing its page gives it its record
    ws.write_page(
        "hollis-bakery-operations-handbook",
        tag_page(summary, "qwen2.5:3b", on="2026-11-18", source=raw),
    )
    ws.text.mkdir(exist_ok=True)
    (ws.text / f"{raw}.txt").write_text(text, encoding="utf-8")
    doc_index.sync_workspace(ws.root)
    return ws


class _Backend:
    """Records the prompt and answers with a fixed sentence."""

    model = "fake"

    def __init__(self):
        self.prompts: list[str] = []

    def chat_stream(self, messages, **k):
        self.prompts.append("\n".join(m.content for m in messages))
        yield "ok"

    def chat(self, messages, **k):
        self.prompts.append("\n".join(m.content for m in messages))
        return "ok"


MILL = "Which mill does Hollis Bakery buy its flour from?"
PRICE = "By how much does the butter price rise if the order is placed after Wednesday noon?"


# ── the passages ──────────────────────────────────────────────────────────────


def test_a_question_the_document_answers_gets_its_passage_with_its_page(tmp_path, embeds):
    ws = _wiki(tmp_path)
    hits = doc_context.document_passages([ws], PRICE)
    assert hits and "eight percent" in hits[0].text and hits[0].page == 2
    assert (
        doc_context.passage_label(hits[0]) == "hollis-bakery-handbook.pdf, page 2"
    )  # no hash in the name


@pytest.mark.parametrize(
    "question",
    [
        "What is the capital of France?",
        "Hello, how are you today?",
        "Write me a short poem about the sea.",
    ],
)
def test_a_question_the_documents_do_not_answer_adds_nothing(tmp_path, embeds, question):
    assert doc_context.document_passages([_wiki(tmp_path)], question) == []


def test_nothing_is_searched_when_there_is_no_index_and_none_is_created(tmp_path, embeds):
    ws = Workspace(tmp_path / "empty", scope="personal")
    ws.init()
    assert doc_context.document_passages([ws, None], MILL) == []
    assert not (ws.root / ".cache" / doc_index.DB_NAME).exists()


def test_it_can_be_switched_off(tmp_path, embeds, monkeypatch):
    ws = _wiki(tmp_path)
    monkeypatch.setattr(doc_context, "ENABLED", False)
    assert doc_context.document_passages([ws], PRICE) == []


def test_only_the_workspaces_that_ground_the_chat_are_searched(tmp_path, embeds):
    personal, team = _wiki(tmp_path, "personal"), Workspace(tmp_path / "team", scope="team")
    team.init()
    assert doc_context.document_passages([team], PRICE) == []  # the document is in the other wiki
    assert doc_context.document_passages([team, personal], PRICE)


def test_at_most_two_passages(tmp_path, embeds):
    filler = [
        f"Paragraph {i} about topic number {i} with words that are only in this one. " * 8
        for i in range(9)
    ]
    wanted = [
        "The Brandt mill delivery arrives at dawn. " * 15 + f"Detail {i} is unique {i}."
        for i in range(3)
    ]
    ws = _wiki(tmp_path, text="\n\n".join(filler + wanted))
    hits = doc_context.document_passages([ws], "When does the Brandt mill delivery arrive?")
    assert len(hits) == 2 and all("Brandt" in h.text for h in hits)


# ── the labels ────────────────────────────────────────────────────────────────


def test_the_summary_of_the_document_is_labelled_and_other_pages_are_not(tmp_path, embeds):
    ws = _wiki(tmp_path)
    ws.write_page("other-note", "# Other note\n\nSomething else.\n")
    hits = doc_context.document_passages([ws], MILL)
    labels = doc_context.summary_labels(ws.pages(), hits)
    page = ws.wiki / "hollis-bakery-operations-handbook.md"
    assert list(labels) == [page]
    assert (
        "AI summary of hollis-bakery-handbook.pdf" in labels[page]
        and "may contain errors" in labels[page]
    )


def test_the_sources_name_each_document_once_with_its_pages():
    class H:
        def __init__(self, doc, page):
            self.doc, self.page = doc, page

    tags = doc_context.source_tags(
        [H("a-1a2b3c4d5e6f.pdf", 2), H("a-1a2b3c4d5e6f.pdf", 1), H("b-1a2b3c4d5e6f.html", None)]
    )
    assert tags == [
        "doc:a-1a2b3c4d5e6f.pdf (page 1, 2)",
        "doc:b-1a2b3c4d5e6f.html",
    ]  # the cache follows these names
    assert doc_context.split_sources(["cabinet", *tags, "other"]) == (
        ["cabinet", "other"],
        ["a.pdf (page 1, 2)", "b.html"],  # and the person sees the names they uploaded
    )
    assert doc_context.stored_name(tags[0]) == "a-1a2b3c4d5e6f.pdf"


# ── the answer's context ──────────────────────────────────────────────────────


def _context(prompt: str) -> str:
    return prompt


def test_the_prompt_has_the_original_first_and_the_summary_labelled(tmp_path, embeds):
    ws, backend = _wiki(tmp_path), _Backend()
    list(ask_mod.ask_stream(ws, MILL, backend))
    prompt = backend.prompts[0]
    assert prompt.index("Brandt mill") < prompt.index("Flurf")  # the original before the summary
    assert "prefer these to any summary" in prompt
    assert "[hollis-bakery-handbook.pdf, page 2]" in prompt
    assert "[AI summary of hollis-bakery-handbook.pdf. It may contain errors" in prompt


def test_the_blocking_answer_does_the_same_and_reports_the_document(tmp_path, embeds):
    ws, backend = _wiki(tmp_path), _Backend()
    _answer, slugs, hit = ask_mod.ask(ws, MILL, backend)
    assert "Brandt mill" in backend.prompts[0] and hit is False
    assert doc_context.split_sources(slugs) == (
        ["hollis-bakery-operations-handbook"],
        ["hollis-bakery-handbook.pdf (page 1, 2)"],
    )


def test_a_fact_the_summary_never_had_is_still_found_with_no_page_at_all(tmp_path, embeds):
    ws, backend = _wiki(tmp_path), _Backend()
    _answer, slugs, _hit = ask_mod.ask(ws, PRICE, backend)
    assert "eight percent" in backend.prompts[0]  # the summary has no price, so no page matched
    assert slugs == [f"doc:{RAW} (page 2)"]


def test_the_streaming_answer_reports_the_document_to_the_page(tmp_path, embeds):
    ws, backend, seen = _wiki(tmp_path), _Backend(), []
    list(ask_mod.ask_stream(ws, PRICE, backend, on_context=seen.extend))
    assert f"doc:{RAW} (page 2)" in seen


def test_an_unrelated_question_has_the_same_prompt_and_the_search_embeds_nothing(
    tmp_path, embeds, monkeypatch
):
    question = "What is the capital of France?"
    ws = _wiki(tmp_path)
    before = len(embeds)
    assert doc_context.document_passages([ws], question) == []
    assert doc_context.document_passages([ws], PRICE)  # and a question it answers
    assert len(embeds) == before  # finding passages never calls the embedding model

    on, off = _Backend(), _Backend()
    list(ask_mod.ask_stream(ws, question, on))
    monkeypatch.setattr(doc_context, "ENABLED", False)
    list(ask_mod.ask_stream(ws, question, off))
    assert on.prompts == off.prompts  # nothing was added


def test_a_wiki_with_no_document_text_gets_exactly_the_old_context(tmp_path, embeds, monkeypatch):
    ws = Workspace(tmp_path / "plain", scope="personal")
    ws.init()
    ws.write_page("bakery", "# Bakery\n\nThe flour comes from the Brandt mill.\n")
    on, off = _Backend(), _Backend()
    list(ask_mod.ask_stream(ws, MILL, on))
    monkeypatch.setattr(doc_context, "ENABLED", False)
    list(ask_mod.ask_stream(ws, MILL, off))
    assert on.prompts == off.prompts


# ── the cache keeps the document entries ──────────────────────────────────────


def test_a_cached_answer_that_cites_a_document_is_still_served(tmp_path, embeds, monkeypatch):
    from anthill.cache import SemanticCache

    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    ws = _wiki(tmp_path)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(
        PRICE,
        "8 percent",
        slugs=["hollis-bakery-operations-handbook", f"doc:{RAW} (page 2)"],
    )
    cache.judge = ask_mod.cache_judge([ws])
    hit = cache.lookup(PRICE)
    assert hit is not None and f"doc:{RAW} (page 2)" in hit.slugs


# ── what the chat shows and keeps ─────────────────────────────────────────────


def test_documents_are_stored_whole_one_per_line():
    assert (
        app_mod._docs_for_storage(["a.pdf (page 2)", "b, final.html"])
        == "a.pdf (page 2)\nb, final.html"
    )
    assert app_mod._docs_for_storage([]) == "" and app_mod._docs_for_storage(None) == ""
    many = [f"document-{i:03d}-" + "x" * 40 + ".pdf" for i in range(30)]
    out = app_mod._docs_for_storage(many)
    assert len(out) <= 500 and out.split("\n") == many[: len(out.split("\n"))]


@pytest.fixture
def chat(tmp_path, monkeypatch, embeds):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="a@a.com", role="admin", active=True)
    s.add_all([user, OrgSettings(org_id=org.id, deployment_topology="solo")])
    s.commit()
    uid, org_id = user.id, org.id
    s.close()

    personal = workspace_for("personal", user_id=uid)
    personal.init()
    (personal.raw / RAW).write_bytes(
        b"%PDF-1.4 handbook"
    )  # the original is kept; writing its page gives it its record
    personal.write_page(
        "hollis-bakery-operations-handbook",
        tag_page(SUMMARY, "qwen2.5:3b", on="2026-11-18", source=RAW),
    )
    personal.text.mkdir()
    (personal.text / f"{RAW}.txt").write_text(HANDBOOK, encoding="utf-8")
    doc_index.sync_workspace(personal.root)

    state = {"prompts": [], "fail": False}

    def _model(backend, messages, *a, **k):
        state["prompts"].append("\n".join(m.content for m in messages))
        if state["fail"]:
            raise RuntimeError("the model is not running")
        yield "The flour comes from the Brandt mill."

    monkeypatch.setattr(ask_mod, "stream_chat", _model)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "admin"))

    def ask(message: str) -> int:
        sess = app_mod._SessionFactory()
        conv = Conversation(user_id=uid, org_id=org_id, plane="solo", title="t")
        sess.add(conv)
        sess.commit()
        conv_id = conv.id
        sess.close()
        assert client.get(f"/chat/{conv_id}/stream", params={"message": message}).status_code == 200
        return conv_id

    def row(conv_id: int) -> ChatMessage:
        sess = app_mod._SessionFactory()
        try:
            return (
                sess.query(ChatMessage)
                .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
                .one()
            )
        finally:
            sess.close()

    return types.SimpleNamespace(ask=ask, row=row, client=client, state=state)


def _meta(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts = [
        n.get_text()
        for n in soup.select("div.msg.assistant .meta, div.msg.assistant .answer-record")
    ]
    return " ".join(" ".join(parts).split())


def test_the_chat_keeps_and_shows_the_document_an_answer_was_given(chat):
    conv = chat.ask(MILL)
    saved = chat.row(conv)
    assert saved.doc_sources == "hollis-bakery-handbook.pdf (page 1, 2)"
    assert (
        saved.wiki_slugs == "hollis-bakery-operations-handbook"
    )  # the page is still listed as a page
    assert "Brandt mill" in chat.state["prompts"][-1]

    shown = _meta(chat.client.get(f"/chat/{conv}").text)
    assert "What this answer was given (2)" in shown  # the panel (#281)
    assert "hollis-bakery-handbook.pdf · Document, Your wiki, page 1, 2" in shown
    assert "Hollis Bakery: Operations Handbook · Page, Your wiki" in shown


def test_a_document_alone_is_shown_when_no_page_matched(chat):
    conv = chat.ask(PRICE)
    saved = chat.row(conv)
    assert saved.wiki_slugs == "" and saved.doc_sources == "hollis-bakery-handbook.pdf (page 2)"
    assert "eight percent" in chat.state["prompts"][-1]
    shown = _meta(chat.client.get(f"/chat/{conv}").text)
    assert "What this answer was given (1)" in shown
    assert "hollis-bakery-handbook.pdf · Document, Your wiki, page 2" in shown
    assert "Page, Your wiki" not in shown


def test_the_live_stream_reports_the_documents(chat):
    conv = chat.ask(MILL)
    stream = chat.client.get(f"/chat/{conv}/stream", params={"message": MILL}).text
    docs = [
        json.loads(line[6:])["meta"]["documents"]
        for line in stream.splitlines()
        if line.startswith("data: {") and '"documents"' in line
    ]
    assert docs and docs[0] == ["hollis-bakery-handbook.pdf (page 1, 2)"]
    page = chat.client.get(f"/chat/{conv}").text
    assert (
        "Documents in context: ${d.meta.documents.join(', ')}" in page
    )  # and the page knows how to show them


def test_a_question_no_document_answers_saves_no_documents(chat):
    conv = chat.ask("Hello, how are you today?")
    assert chat.row(conv).doc_sources == ""
    assert "Documents in context" not in _meta(chat.client.get(f"/chat/{conv}").text)


def test_a_failed_turn_saves_no_documents(chat):
    chat.state["fail"] = True
    conv = chat.ask(PRICE)
    saved = chat.row(conv)
    assert saved.generation_failed is True and saved.doc_sources == ""


# ── the cache follows the documents an answer used ────────────────────────────


def _cache(ws, question, answer, slugs):
    import os
    import time

    from anthill.cache import SemanticCache

    os.environ.pop("ANTHILL_DISABLE_SEMANTIC_CACHE", None)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(question, answer, slugs=slugs)
    cache.judge = ask_mod.cache_judge([ws])
    time.sleep(0.05)  # whatever changes next changes after the answer was made
    return cache


def _touch(path, text):
    path.write_text(text, encoding="utf-8")


DOC_ENTRY = f"doc:{RAW} (page 2)"


def test_a_cached_answer_is_dropped_when_the_text_it_was_given_changes(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = _cache(ws, PRICE, "8 percent", [DOC_ENTRY])
    assert cache.lookup(PRICE) is not None  # nothing changed

    _touch(ws.text / f"{RAW}.txt", HANDBOOK.replace("eight percent", "ten percent"))
    assert cache.lookup(PRICE) is None
    assert cache._store.count() == 0  # and it is gone, not just skipped


def test_a_cached_answer_is_not_served_when_the_text_it_was_given_is_gone(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = _cache(ws, PRICE, "8 percent", [DOC_ENTRY])
    text = (ws.text / f"{RAW}.txt").read_text(encoding="utf-8")
    (ws.text / f"{RAW}.txt").unlink()
    assert cache.lookup(PRICE) is None
    assert cache._store.count() == 1  # kept: the text may be in the wiki of another scope

    (ws.text / f"{RAW}.txt").write_text(text, encoding="utf-8")
    import os

    os.utime(ws.text / f"{RAW}.txt", (1, 1))  # as old as it was
    assert cache.lookup(PRICE) is not None


def test_an_answer_that_did_not_know_is_dropped_once_the_document_that_answers_it_arrives(
    tmp_path, embeds
):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    cache = _cache(ws, PRICE, "The information is not provided.", [])
    assert cache.lookup(PRICE) is not None

    ws.text.mkdir(exist_ok=True)
    (ws.text / f"{RAW}.txt").write_text(HANDBOOK, encoding="utf-8")
    doc_index.sync_workspace(ws.root)  # the document is uploaded and indexed
    assert cache.lookup(PRICE) is None


def test_a_new_document_about_something_else_keeps_the_cached_answer(tmp_path, embeds):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    cache = _cache(ws, PRICE, "The information is not provided.", [])
    ws.text.mkdir(exist_ok=True)
    other = "Riverside cycling club handbook\n\nRides leave at 8:00 on Saturdays from the boathouse car park."
    (ws.text / "club-1a2b3c4d5e6f.html.txt").write_text(other, encoding="utf-8")
    doc_index.sync_workspace(ws.root)
    assert cache.lookup(PRICE) is not None


def test_a_new_text_that_is_not_indexed_yet_is_not_assumed_to_be_irrelevant(tmp_path, embeds):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    cache = _cache(ws, PRICE, "The information is not provided.", [])
    ws.text.mkdir(exist_ok=True)
    (ws.text / f"{RAW}.txt").write_text(
        HANDBOOK, encoding="utf-8"
    )  # kept, the index has not caught up
    assert cache.lookup(PRICE) is None


# ── scope ─────────────────────────────────────────────────────────────────────


def test_a_document_in_a_wiki_outside_the_chats_scope_is_not_used(tmp_path, embeds):
    personal = _wiki(tmp_path, "personal")
    team = Workspace(tmp_path / "team", scope="team")
    team.init()
    only_team, with_personal = _Backend(), _Backend()
    ask_mod.ask(team, PRICE, only_team)  # a team chat does not see the personal wiki
    ask_mod.ask(team, PRICE, with_personal, extra_workspaces=[personal])  # unless it is blended in
    assert "eight percent" not in only_team.prompts[0]
    assert "eight percent" in with_personal.prompts[0]


# ── a database from before the column existed ─────────────────────────────────


def test_a_database_from_before_doc_sources_opens_and_its_old_answers_show_no_documents(tmp_path):
    from sqlalchemy import text

    from anthill.web import migrate

    eng = create_engine(
        f"sqlite:///{tmp_path / 'old.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    sess = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    sess.add(org)
    sess.flush()
    user = User(org_id=org.id, email="a@a.com", role="admin", active=True)
    sess.add(user)
    sess.flush()
    conv = Conversation(user_id=user.id, org_id=org.id, plane="solo", title="t")
    sess.add(conv)
    sess.flush()
    sess.add(
        ChatMessage(
            conversation_id=conv.id,
            role="assistant",
            content="An old answer.",
            model="m",
            wiki_slugs="cabinet",
        )
    )
    sess.commit()
    sess.close()
    with eng.begin() as con:
        con.execute(
            text("ALTER TABLE chat_messages DROP COLUMN doc_sources")
        )  # the database as it was

    assert "chat_messages.doc_sources" in migrate.ensure_columns(eng)  # added at startup

    sess = sessionmaker(bind=eng)()
    old = sess.query(ChatMessage).one()
    assert old.doc_sources == "" and old.wiki_slugs == "cabinet" and old.content == "An old answer."
    sess.close()


# ── when document search is off ───────────────────────────────────────────────


def test_the_probe_reports_why_an_sqlite_cannot_run_the_index_and_remembers_it(monkeypatch):
    import sqlite3

    calls = []

    class _Old:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError('unrecognized option: "contentless_delete"')

        def close(self):
            pass

    def _connect(*a, **k):
        calls.append(a)
        return _Old()

    monkeypatch.setattr(doc_index, "_support", None)
    monkeypatch.setattr(doc_index.sqlite3, "connect", _connect)
    ok, why = doc_index.support()
    assert ok is False and sqlite3.sqlite_version in why and "contentless_delete" in why
    assert doc_index.support() == (ok, why) and len(calls) == 1  # asked once, then remembered


def test_a_person_is_told_in_plain_words_and_the_raw_error_stays_in_the_log(monkeypatch):
    monkeypatch.setattr(doc_index, "_support", (False, 'unrecognized option: "contentless_delete"'))
    note = doc_index.off_notice()
    assert note.startswith(
        "Document search is off on this install, so answers use the AI's summary pages only."
    )
    assert "SQLite 3.43 or newer" in note and "Updating Anthill fixes it" in note
    assert "contentless_delete" not in note and "unrecognized" not in note
    monkeypatch.setattr(doc_index, "_support", (True, ""))
    assert doc_index.off_notice() == ""


def test_this_sqlite_can_run_the_index():
    assert doc_index.support() == (True, "")


def test_when_the_sqlite_cannot_run_it_the_startup_pass_warns_and_nothing_is_queued(
    monkeypatch, caplog, tmp_path
):
    import logging
    import queue

    monkeypatch.setattr(
        doc_index,
        "_support",
        (False, "this SQLite (3.31.1) cannot build it; it needs 3.43 with FTS5"),
    )
    monkeypatch.setattr(doc_index, "ENABLED", True)
    q: queue.Queue = queue.Queue()
    monkeypatch.setattr(doc_index, "_pending", q)
    monkeypatch.setattr(doc_index, "_ensure_worker", lambda: None)
    with caplog.at_level(logging.WARNING, logger="anthill.wiki.doc_index"):
        assert doc_index.sync_all() == 0
    assert (
        "document search is off" in caplog.text and "3.31.1" in caplog.text
    )  # at warning level, not debug
    doc_index.index_soon(tmp_path, tmp_path / "x.txt")
    assert q.qsize() == 0


def test_the_files_tab_says_when_document_search_is_off(chat, monkeypatch):
    html = chat.client.get("/wiki?tab=files").text
    assert "Document search is off" not in html

    monkeypatch.setattr(
        doc_index,
        "_support",
        (False, "this SQLite (3.31.1) cannot build it; it needs 3.43 with FTS5"),
    )
    html = chat.client.get("/wiki?tab=files").text
    note = BeautifulSoup(html, "html.parser").select_one("#doc-search-off")
    assert note is not None and "summary pages only" in note.get_text()
    assert "SQLite 3.43 or newer" in note.get_text()  # in plain words
    assert (
        "cannot build it" not in note.get_text() and "FTS5" not in note.get_text()
    )  # the log line is not shown
