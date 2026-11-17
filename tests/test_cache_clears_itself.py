"""The answer cache clears itself. A cached answer is dropped when the wiki pages it was built from change,
is not served when a page it cited is gone, and storing a question again replaces its old answer. An
unchanged wiki keeps hitting the cache, so none of this costs a local model a single extra call. Model-free:
the embedding model is a fake, the cache is the real one."""

import hashlib
import json
import os
import re
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.wiki.ask as wiki_ask
from anthill.cache import SemanticCache
from anthill.cache import embedder as emb
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import Conversation, Organization, OrgSettings, User
from anthill.wiki.workspace import Workspace

QUESTION = "what is the vault code for the maintenance cabinet"
PAGE = "# Cabinet\n\nFor the maintenance cabinet, the vault code is 4417-ZEBRA.\n"
LONG_AGO, LATER = -1000.0, 1000.0  # seconds from now: a page older than any answer, and one newer


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

    getattr(emb, "_recent", {}).clear()  # the reuse layer, if this tree has it
    monkeypatch.setattr(emb, "embed", _embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    # The background job that embeds a saved page is not what these tests are about.
    monkeypatch.setattr("anthill.wiki.page_index.warm_page_soon", lambda *a, **k: None)
    yield calls
    getattr(emb, "_recent", {}).clear()  # the reuse layer, if this tree has it


def _age(path, seconds_from_now: float) -> None:
    t = time.time() + seconds_from_now
    os.utime(path, (t, t))


def _wiki(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    _age(ws.write_page("cabinet", PAGE), LONG_AGO)
    return ws


def _lookup(cache, ws):
    cache.judge = wiki_ask.cache_judge([ws])
    return cache.lookup(QUESTION)


# ── the same question stored again ────────────────────────────────────────────


def test_storing_a_question_again_replaces_its_answer(tmp_path, embeds):
    cache = SemanticCache(db_path=tmp_path / "c")
    cache.store(QUESTION, "the first answer")
    cache.store(QUESTION, "the second answer")
    assert cache._store.count() == 1
    assert cache.lookup(QUESTION).answer == "the second answer"


# ── a wiki that has not changed ───────────────────────────────────────────────


def test_an_unchanged_wiki_keeps_serving_the_answer_without_embedding_a_page(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "the code is 4417-ZEBRA", slugs=["cabinet"])
    embeds.clear()

    assert _lookup(cache, ws).answer == "the code is 4417-ZEBRA"
    assert set(embeds) <= {QUESTION}  # at most the question: no page was embedded to decide this


# ── a page that changed ───────────────────────────────────────────────────────


def test_a_changed_cited_page_drops_the_answer(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "the code is 4417-ZEBRA", slugs=["cabinet"])

    _age(ws.write_page("cabinet", "# Cabinet\n\nThe vault code is now 9090-OTTER.\n"), LATER)
    assert _lookup(cache, ws) is None
    assert cache._store.count() == 0  # and it is gone, not just skipped


def test_a_changed_page_that_was_not_cited_and_is_unrelated_keeps_the_answer(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "the code is 4417-ZEBRA", slugs=["cabinet"])

    _age(ws.write_page("lunch", "# Lunch\n\nSoup is served on Tuesdays.\n"), LATER)
    assert _lookup(cache, ws).answer == "the code is 4417-ZEBRA"


def test_a_new_page_about_the_question_drops_an_answer_made_without_it(tmp_path, embeds):
    """An answer made before the wiki knew anything about the question must not outlive the page that
    now does."""
    ws = Workspace(tmp_path / "w")
    ws.init()
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "I do not know the vault code")  # no page grounded it

    _age(ws.write_page("cabinet", PAGE), LATER)
    assert _lookup(cache, ws) is None
    assert cache._store.count() == 0


def test_a_new_page_about_something_else_keeps_an_answer_made_without_pages(tmp_path, embeds):
    ws = Workspace(tmp_path / "w")
    ws.init()
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "an answer from general knowledge")

    _age(ws.write_page("lunch", "# Lunch\n\nSoup is served on Tuesdays.\n"), LATER)
    assert _lookup(cache, ws).answer == "an answer from general knowledge"


# ── a page that is gone ───────────────────────────────────────────────────────


def test_an_answer_citing_a_missing_page_is_not_served_but_is_kept(tmp_path, embeds):
    ws = _wiki(tmp_path)
    cache = SemanticCache(db_path=ws.root / ".cache")
    cache.store(QUESTION, "the code is 4417-ZEBRA", slugs=["cabinet"])
    page = ws.wiki / "cabinet.md"
    text = page.read_text()
    page.unlink()

    assert _lookup(cache, ws) is None
    assert cache._store.count() == 1  # it may belong to a chat whose scope still has the page

    page.write_text(text)
    _age(page, LONG_AGO)
    assert _lookup(cache, ws).answer == "the code is 4417-ZEBRA"


# ── one chat turn, end to end ─────────────────────────────────────────────────


def _answer_text(stream: str) -> str:
    out = []
    for line in stream.splitlines():
        if line.startswith("data: "):
            try:
                out.append(str(json.loads(line[6:]).get("token", "")))
            except ValueError:
                pass
    return "".join(out)


def test_chat_serves_the_cache_until_the_wiki_changes_and_then_answers_afresh(
    tmp_path, monkeypatch, embeds
):
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
    s.flush()
    uid, org_id = user.id, org.id
    s.commit()
    s.close()

    from anthill.wiki.workspace import workspace_for

    personal = workspace_for("personal", user_id=uid)
    personal.init()
    _age(personal.write_page("cabinet", PAGE), LONG_AGO)

    calls: list[str] = []

    def _model(backend, messages, *a, **k):
        calls.append("model")
        yield "ANSWER BUILT FROM: " + " | ".join(m.content for m in messages)

    monkeypatch.setattr(wiki_ask, "stream_chat", _model)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "admin"))

    def ask() -> str:
        sess = app_mod._SessionFactory()
        conv = Conversation(user_id=uid, org_id=org_id, plane="solo", title="t")
        sess.add(conv)
        sess.commit()
        conv_id = conv.id
        sess.close()
        r = client.get(f"/chat/{conv_id}/stream", params={"message": QUESTION})
        assert r.status_code == 200
        return _answer_text(r.text)

    first = ask()
    assert "4417-ZEBRA" in first and calls == ["model"]

    assert ask() == first and calls == [
        "model"
    ]  # nothing changed: served from the cache, no model call

    time.sleep(0.05)  # a real edit, made after the first answer was cached
    personal.write_page("cabinet", "# Cabinet\n\nThe vault code is now 9090-OTTER.\n")
    changed = ask()
    assert calls == ["model", "model"]  # the page changed: answered afresh
    assert "9090-OTTER" in changed and "4417-ZEBRA" not in changed

    assert ask() == changed and calls == ["model", "model"]  # and the new answer is cached again
