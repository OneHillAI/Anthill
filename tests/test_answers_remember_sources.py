"""An answer keeps what it was given. The chat page showed the wiki pages an answer was grounded on, and a
"cache hit" badge, only while the answer streamed: the saved message never recorded either (the columns
existed and nothing wrote them), so a reload showed an answer with no sources. Model-free: the model is a
fake and the embedding model is a counting bag-of-words."""

import hashlib
import json
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.wiki.ask as wiki_ask
from anthill.cache import embedder as emb
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import ChatMessage, Conversation, Organization, OrgSettings, User
from anthill.wiki.workspace import workspace_for

QUESTION = "what is the vault code for the maintenance cabinet"
PAGE = "# Cabinet\n\nFor the maintenance cabinet, the vault code is 4417-ZEBRA.\n"


def _vector(text: str) -> np.ndarray:
    vec = np.zeros(emb.DIM, dtype=np.float32)
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % emb.DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A Solo user with one personal wiki page, and a function that asks one question in a new chat."""
    getattr(emb, "_recent", {}).clear()
    monkeypatch.setattr(emb, "embed", _vector)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    monkeypatch.setattr("anthill.wiki.page_index.warm_page_soon", lambda *a, **k: None)
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
    personal.write_page("cabinet", PAGE)

    state = {"model_calls": 0, "fail": False}

    def _model(backend, messages, *a, **k):
        state["model_calls"] += 1
        if state["fail"]:
            raise RuntimeError("the model is not running")
        yield "The vault code is 4417-ZEBRA."

    monkeypatch.setattr(wiki_ask, "stream_chat", _model)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "admin"))

    def ask(message: str = QUESTION) -> int:
        sess = app_mod._SessionFactory()
        conv = Conversation(user_id=uid, org_id=org_id, plane="solo", title="t")
        sess.add(conv)
        sess.commit()
        conv_id = conv.id
        sess.close()
        r = client.get(f"/chat/{conv_id}/stream", params={"message": message})
        assert r.status_code == 200
        return conv_id

    def answer_row(conv_id: int) -> ChatMessage:
        sess = app_mod._SessionFactory()
        try:
            return (
                sess.query(ChatMessage)
                .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
                .one()
            )
        finally:
            sess.close()

    yield types_namespace(ask=ask, answer_row=answer_row, client=client, state=state)
    getattr(emb, "_recent", {}).clear()


def types_namespace(**kw):
    import types

    return types.SimpleNamespace(**kw)


def _sources_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    parts = [
        n.get_text()
        for n in soup.select("div.msg.assistant .meta, div.msg.assistant .answer-record")
    ]
    return " ".join(" ".join(parts).split())


def test_an_answer_keeps_the_pages_it_was_given(chat):
    conv_id = chat.ask()
    row = chat.answer_row(conv_id)
    assert row.wiki_slugs == "cabinet" and row.cache_hit is False

    reloaded = _sources_text(chat.client.get(f"/chat/{conv_id}").text)
    assert "What this answer was given (1) Cabinet" in reloaded  # the panel (#281) lists the page
    assert "cache hit" not in reloaded


def test_a_cached_answer_keeps_its_cache_badge_and_its_pages(chat):
    first = chat.ask()
    second = chat.ask()
    assert chat.state["model_calls"] == 1  # the second was served from the cache

    row = chat.answer_row(second)
    assert row.cache_hit is True and row.wiki_slugs == "cabinet"
    assert chat.answer_row(first).cache_hit is False

    reloaded = _sources_text(chat.client.get(f"/chat/{second}").text)
    assert "cache hit" in reloaded and "What this answer was given (1) Cabinet" in reloaded


def test_the_page_after_a_reload_says_what_the_live_page_says(chat):
    """The live stream and the reloaded page word it the same way, and neither says "cited": the pages are
    what the model was given, not a claim about what it relied on."""
    conv_id = chat.ask()
    live = chat.client.get(f"/chat/{conv_id}/stream", params={"message": QUESTION}).text
    sent = [
        json.loads(line[6:]).get("meta", {}).get("wiki_slugs")
        for line in live.splitlines()
        if line.startswith("data: {") and "wiki_slugs" in line
    ]
    assert sent and sent[0] == ["cabinet"]

    html = chat.client.get(f"/chat/{conv_id}").text
    assert "Pages in context:" in html and "cited" not in _sources_text(html).lower()
    assert (
        "Pages in context: ${pageLinks(d.meta.wiki_slugs)}" in html
    )  # the live page's own wording, with each page a link
    assert "It does not show which of them the answer relied on." in html


def test_a_question_that_used_no_page_saves_no_sources(chat):
    conv_id = chat.ask("hello there my friend how are you today")
    row = chat.answer_row(conv_id)
    assert row.wiki_slugs == "" and row.cache_hit is False
    assert "Pages in context" not in _sources_text(chat.client.get(f"/chat/{conv_id}").text)


def test_a_failed_turn_saves_no_sources_and_no_badge(chat):
    chat.state["fail"] = True
    conv_id = chat.ask()
    row = chat.answer_row(conv_id)
    assert row.generation_failed is True
    assert row.wiki_slugs == "" and row.cache_hit is False


@pytest.mark.parametrize(
    ("slugs", "stored"),
    [
        (["a", "b"], "a,b"),
        ([], ""),
        (None, ""),
        (["a", "", "b"], "a,b"),
        (["has,comma", "ok"], "ok"),  # a comma would split one slug into two on the way back
    ],
)
def test_slugs_are_stored_as_a_comma_separated_string(slugs, stored):
    assert app_mod._slugs_for_storage(slugs) == stored


def test_storage_stops_at_the_column_width_on_a_whole_slug():
    slugs = [f"page-{i:03d}-" + "x" * 40 for i in range(20)]
    out = app_mod._slugs_for_storage(slugs)
    assert len(out) <= 500
    kept = out.split(",")
    assert kept == slugs[: len(kept)] and all(len(s) == len(slugs[0]) for s in kept)


def test_each_page_in_context_is_a_link_that_opens_the_page(chat):
    conv_id = chat.ask()
    from bs4 import BeautifulSoup

    meta = BeautifulSoup(chat.client.get(f"/chat/{conv_id}").text, "html.parser").select_one(
        "div.msg.assistant"
    )
    link = meta.find("a", string="Cabinet")
    assert link is not None and link["href"] == "/wiki/page/cabinet?scope=personal"
    assert link["target"] == "_blank" and "noopener" in link["rel"]
    page = chat.client.get(link["href"])  # the panel knows which wiki holds the page
    assert page.status_code == 200 and "4417-ZEBRA" in page.text
    # the link an older answer still uses finds the page in whichever wiki holds it
    r = chat.client.get("/wiki/open/cabinet", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/wiki/page/cabinet?scope=personal"


def test_a_page_that_is_gone_says_so_instead_of_failing_quietly(chat):
    assert chat.client.get("/wiki/open/no-such-page", follow_redirects=False).status_code == 404
