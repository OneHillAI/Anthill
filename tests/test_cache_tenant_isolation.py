"""The SHARED (org-plane) semantic cache must not serve or store a personalized answer.

An org-plane answer grounded in the requester's team wikis (or personal profile) is specific to that
user. The org cache is one shared store keyed by the question embedding, so caching such an answer
would serve it - plus its grounding slugs - to a different org user who lacks that access. Personal-
plane caches are per-user and must keep working normally.
"""

import numpy as np

from anthill.cache import embedder as emb
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


class _Backend:
    def chat(self, messages, **k):
        return "a concrete grounded answer"


def _mock_cache(monkeypatch):
    calls = {"lookup": 0, "store": 0}

    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            calls["lookup"] += 1
            return None

        def store(self, q, a, slugs=None):
            calls["store"] += 1

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    monkeypatch.setattr(emb, "safe_embed", lambda p: np.ones(1024, dtype=np.float32))
    return calls


def test_shared_cache_skips_team_grounded_answer(tmp_path, monkeypatch):
    calls = _mock_cache(monkeypatch)
    org = Workspace(tmp_path / "org")
    org.init()
    team = Workspace(tmp_path / "team")
    team.init()
    # shared cache + a team wiki blended in -> the answer is personalized: no lookup, no store
    ask_mod.ask(
        org, "what did the team decide?", _Backend(), shared_cache=True, extra_workspaces=[team]
    )
    assert calls == {"lookup": 0, "store": 0}


def test_shared_cache_skips_profile_grounded_answer(tmp_path, monkeypatch):
    calls = _mock_cache(monkeypatch)
    org = Workspace(tmp_path / "org")
    org.init()
    ask_mod.ask(
        org, "summarise this", _Backend(), shared_cache=True, profile="I prefer terse bullets"
    )
    assert calls["store"] == 0  # a profile-personalized answer is never put in the shared cache


def test_shared_cache_still_caches_plain_org_answers(tmp_path, monkeypatch):
    calls = _mock_cache(monkeypatch)
    org = Workspace(tmp_path / "org")
    org.init()
    # shared cache, no team wiki, no profile -> a plain org-wide answer, safe to share: cache normally
    ask_mod.ask(org, "what is our refund policy?", _Backend(), shared_cache=True)
    assert calls["lookup"] >= 1 and calls["store"] == 1


def test_personal_plane_cache_keeps_personalising(tmp_path, monkeypatch):
    calls = _mock_cache(monkeypatch)
    ws = Workspace(tmp_path / "user")
    ws.init()
    # personal plane (shared_cache=False) is a per-user store: profile personalization is fine to cache
    ask_mod.ask(ws, "what did I plan?", _Backend(), shared_cache=False, profile="prefs")
    assert calls["store"] == 1
