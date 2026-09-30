"""Cache provenance + cold-cache behaviour (chat-eval follow-ups from PR #310).

- A cached grounded answer must return its ORIGINAL wiki slugs on a hit, not [] - otherwise a cache
  hit looks ungrounded (masks the retrieval signal / grounding checks).
- An unanswerable question asked twice must NOT serve a stale cached deflection - deflections are not
  cached (PR #310), so the second ask recomputes.
"""

import numpy as np

from anthill.cache.cache import CacheResult
from anthill.cache.store import CacheStore
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


def test_cache_store_round_trips_slugs(tmp_path):
    store = CacheStore(tmp_path / ".cache")
    vec = np.ones(1024, dtype=np.float32)
    store.add("which db?", vec, "Postgres.", slugs=["db", "architecture"])
    rows = store.search(vec, threshold=0.5)
    assert rows and rows[0].answer == "Postgres."
    assert rows[0].slugs == ["db", "architecture"]  # provenance persisted


def test_cache_store_defaults_slugs_to_empty(tmp_path):
    store = CacheStore(tmp_path / ".cache")
    vec = np.ones(1024, dtype=np.float32)
    store.add("q", vec, "a")  # no slugs (web/org/ungrounded answer)
    rows = store.search(vec, threshold=0.5)
    assert rows and rows[0].slugs == []


class _Backend:
    def __init__(self, reply="fresh answer"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **k):
        self.calls += 1
        return self.reply


def test_ask_cache_hit_returns_grounding_slugs(tmp_path, monkeypatch):
    """A cache hit must carry the answer's original grounding slugs, not an empty list."""
    ws = Workspace(tmp_path / "w")
    ws.init()

    class _Cache:
        def __init__(self, **k):
            pass

        def store(self, q, a, slugs=None):
            pass

        def lookup(self, q):
            return CacheResult("cached answer", 1.0, q, slugs=["refund-policy"])

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    answer, slugs, hit = ask_mod.ask(ws, "what is the refund window?", _Backend())
    assert hit is True and answer == "cached answer"
    assert slugs == ["refund-policy"]  # provenance preserved on the hit, not []


def test_unanswerable_asked_twice_is_not_a_stale_cached_deflection(tmp_path, monkeypatch):
    """Ask an unanswerable question twice: the deflection is never cached, so the second ask reaches
    the backend and returns the (now real) answer instead of a stale cached dead-end."""
    ws = Workspace(tmp_path / "w")
    ws.init()

    stored: dict[str, str] = {}

    class _Cache:  # a real-ish store/lookup over a dict
        def __init__(self, **k):
            pass

        def store(self, q, a, slugs=None):
            stored[q] = a

        def lookup(self, q):
            return CacheResult(stored[q], 1.0, q) if q in stored else None

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)

    first = _Backend("I don't have that information.")
    ans1, _slugs1, hit1 = ask_mod.ask(ws, "what is the airspeed of an unladen swallow?", first)
    assert "don't have that information" in ans1 and hit1 is False
    assert stored == {}  # the deflection was NOT cached

    second = _Backend("About 24 miles per hour.")
    ans2, _slugs2, hit2 = ask_mod.ask(ws, "what is the airspeed of an unladen swallow?", second)
    assert hit2 is False and second.calls == 1  # recomputed, not served from cache
    assert ans2 == "About 24 miles per hour."  # a real answer, not the stale deflection
