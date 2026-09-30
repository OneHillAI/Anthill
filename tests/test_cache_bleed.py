"""Cross-conversation context bleed fixes (chat-eval deep-testing finding, 2026-07-08).

Two vectors let a fresh conversation surface other conversations' content:
  1. the semantic cache matching a short unrelated query at the loose 0.93 floor, and
  2. wiki retrieval grounding on the "closest" page even when nothing is actually relevant.
Plus: the org's cache_threshold setting was never wired into the ask() path, so raising it had no
effect. These tests pin the fixes, including per-user cache isolation (the cross-USER leak surface)."""

import numpy as np

from anthill.cache import embedder as emb
from anthill.cache.cache import DEFAULT_THRESHOLD, SHORT_QUERY_THRESHOLD, SemanticCache
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


class _Backend:
    def __init__(self, reply="fresh answer"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **k):
        self.calls += 1
        return self.reply


# ── short-query cache precision ────────────────────────────────────────────────


def test_short_query_demands_a_stricter_threshold(tmp_path):
    c = SemanticCache(db_path=tmp_path / ".c", threshold=0.90)
    assert c._effective_threshold("book the room") == SHORT_QUERY_THRESHOLD  # 3 terms -> stricter
    assert c._effective_threshold("what database did the platform team choose last quarter") == 0.90


def test_effective_threshold_never_lowers_a_raised_setting(tmp_path):
    # raising the threshold to disable the cache must hold even for short queries
    c = SemanticCache(db_path=tmp_path / ".c", threshold=1.01)
    assert c._effective_threshold("hi there") == 1.01


def test_lookup_searches_with_the_effective_threshold(tmp_path, monkeypatch):
    c = SemanticCache(db_path=tmp_path / ".c", threshold=0.90)
    seen = {}

    class _FakeStore:
        def search(self, vec, threshold):
            seen["t"] = threshold
            return []

    c._store = _FakeStore()
    monkeypatch.setattr(emb, "safe_embed", lambda p: np.ones(4, dtype=np.float32))
    c.lookup("book the room")  # short
    assert seen["t"] == SHORT_QUERY_THRESHOLD
    c.lookup("what database did the platform team pick last quarter please")  # long
    assert seen["t"] == 0.90


# ── cache_threshold is actually wired from the caller ──────────────────────────


def test_ask_threads_cache_threshold_into_the_cache(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    ws.init()
    seen = {}

    class _Cache:
        def __init__(self, **k):
            seen.update(k)

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    ask_mod.ask(ws, "what did we decide?", _Backend(), cache_threshold=1.01)
    assert (
        seen.get("threshold") == 1.01
    )  # the setting reaches the cache (not the hard-coded default)


def test_ask_defaults_to_the_standard_threshold(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    ws.init()
    seen = {}

    class _Cache:
        def __init__(self, **k):
            seen.update(k)

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    ask_mod.ask(ws, "what did we decide?", _Backend())
    assert seen.get("threshold") == DEFAULT_THRESHOLD


# ── grounding relevance floor ──────────────────────────────────────────────────


def test_rank_by_embedding_drops_pages_below_the_grounding_floor(tmp_path, monkeypatch):
    p = tmp_path / "a.md"
    p.write_text("# A\n\nsome unrelated content")
    monkeypatch.setattr(ask_mod.emb, "embed", lambda t: np.ones(4, dtype=np.float32))
    qv = np.ones(4, dtype=np.float32)

    monkeypatch.setattr(ask_mod.emb, "cosine", lambda a, b: 0.20)  # below MIN_GROUNDING_SIM
    assert ask_mod._rank_by_embedding([p], "unrelated query", 3, qv) == []

    monkeypatch.setattr(ask_mod.emb, "cosine", lambda a, b: 0.80)  # clearly related
    assert ask_mod._rank_by_embedding([p], "related query", 3, qv) == [p]


# ── per-user isolation (the cross-USER leak surface) ───────────────────────────


def test_two_users_caches_do_not_bleed(tmp_path, monkeypatch):
    # personal caches live under each user's own workspace root; one user's content must never be
    # served to another. (Same retrieval/cache path where a cross-USER org leak would show up.)
    monkeypatch.setattr(emb, "safe_embed", lambda p: np.ones(1024, dtype=np.float32))
    a = SemanticCache(db_path=tmp_path / "userA" / ".cache", threshold=0.5)
    b = SemanticCache(db_path=tmp_path / "userB" / ".cache", threshold=0.5)
    a.store("the quarterly roadmap ship date decision", "Friday")
    assert a.lookup("the quarterly roadmap ship date decision") is not None  # A sees its own
    assert b.lookup("the quarterly roadmap ship date decision") is None  # B must NOT see A's
