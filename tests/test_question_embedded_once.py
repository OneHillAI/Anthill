"""A chat turn asks the embedding model about its question once, not once per step. The cache lookup,
memory recall, retrieval and the cache write all embed the same text, and each is a call to the model.
Model-free: the embedding model is a counting fake."""

import hashlib
import re
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.memory as mem_mod
import anthill.web.app as app_mod
import anthill.wiki.ask as wiki_ask
from anthill.cache import embedder as emb
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import Conversation, MemoryItem, Organization, OrgSettings, User

QUESTION = "what should I cook for dinner tonight"


def _vector(text: str) -> np.ndarray:
    vec = np.zeros(emb.DIM, dtype=np.float32)
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % emb.DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


@pytest.fixture
def counting_embed(monkeypatch):
    calls: list[str] = []

    def _embed(text: str) -> np.ndarray:
        calls.append(text)
        return _vector(text)

    emb._recent.clear()
    monkeypatch.setattr(emb, "embed", _embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    yield calls
    emb._recent.clear()


# ── the reuse layer ───────────────────────────────────────────────────────────


def test_the_same_text_is_embedded_once(counting_embed):
    first = emb.embed_recent("hello there")
    second = emb.safe_embed("hello there")  # a different caller, same text
    assert counting_embed == ["hello there"]
    assert np.array_equal(first, second)


def test_each_caller_gets_its_own_copy(counting_embed):
    a = emb.embed_recent("hello there")
    a[:] = 0.0  # a caller that changes its vector must not change what the next one gets
    assert float(np.linalg.norm(emb.embed_recent("hello there"))) == pytest.approx(1.0)


def test_a_failure_is_not_remembered(monkeypatch, counting_embed):
    state = {"fail": True}
    real = emb.embed

    def _flaky(text):
        if state["fail"]:
            raise RuntimeError("model busy")
        return real(text)

    monkeypatch.setattr(emb, "embed", _flaky)
    assert emb.safe_embed("hello there") is None
    state["fail"] = False
    assert emb.safe_embed("hello there") is not None


def test_a_result_expires(monkeypatch, counting_embed):
    clock = [1000.0]
    monkeypatch.setattr(emb, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    emb.embed_recent("hello there")
    clock[0] += emb._RECENT_TTL_SECONDS - 1
    emb.embed_recent("hello there")
    assert counting_embed == ["hello there"]  # still remembered
    clock[0] += 2
    emb.embed_recent("hello there")
    assert counting_embed == ["hello there", "hello there"]  # expired, asked again


def test_only_the_last_few_are_kept(counting_embed):
    for i in range(emb._RECENT_MAX + 5):
        emb.embed_recent(f"text number {i}")
    assert len(emb._recent) == emb._RECENT_MAX


# ── one chat turn ─────────────────────────────────────────────────────────────


def test_a_chat_turn_embeds_its_question_once(tmp_path, monkeypatch, counting_embed):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
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
    conv = Conversation(user_id=user.id, org_id=org.id, plane="solo", title="t")
    # A remembered fact, so memory recall has something to rank and embeds the question.
    s.add_all(
        [
            conv,
            MemoryItem(
                org_id=org.id,
                user_id=user.id,
                scope="personal",
                kind="fact",
                text="The user is vegetarian",
                embedding=mem_mod.encode_vec(_vector("The user is vegetarian")),
                source="chat",
            ),
        ]
    )
    s.commit()
    uid, org_id, conv_id = user.id, org.id, conv.id
    s.close()

    monkeypatch.setattr(wiki_ask, "stream_chat", lambda *a, **k: iter(["Make a vegetable curry."]))

    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(uid, org_id, "admin"))
    r = c.get(f"/chat/{conv_id}/stream", params={"message": QUESTION})
    assert r.status_code == 200 and "curry" in r.text.replace('"', "")

    assert counting_embed.count(QUESTION) == 1, counting_embed
