"""Memory v2: semantic dedup, cosine, web-search recall."""

import numpy as np

from anthill import memory as mem

# ── engine: cosine + semantic dedup ──────────────────────────────────────────


def test_cosine():
    a = np.array([1, 0, 0], dtype=np.float32)
    c = np.array([0, 1, 0], dtype=np.float32)
    assert mem.cosine(a, a) == 1.0
    assert mem.cosine(a, c) == 0.0
    assert mem.cosine(a, None) == 0.0 and mem.cosine(None, None) == 0.0


def test_is_semantically_new():
    v = np.array([1, 0, 0], dtype=np.float32)
    near = np.array([0.99, 0.01, 0.0], dtype=np.float32)
    far = np.array([0, 1, 0], dtype=np.float32)
    assert mem.is_semantically_new(v, [far]) is True
    assert mem.is_semantically_new(v, [near], threshold=0.9) is False
    assert mem.is_semantically_new(None, [v]) is True  # no vector -> defer to caller
    assert mem.is_semantically_new(v, []) is True


# ── web-search path now receives memory ──────────────────────────────────────


def test_search_and_answer_includes_memory(monkeypatch):
    import anthill.search.web as web

    monkeypatch.setattr(
        web,
        "web_search",
        lambda *a, **k: [web.SearchResult("Page", "https://example.org/page", "A page.")],
    )
    seen = {}

    class B:
        def chat(self, messages, **kw):
            seen["user"] = messages[-1].content
            return "ok"

    web.search_and_answer("q", backend=B(), memory_context="- prefers metric units")
    assert "prefers metric units" in seen["user"]
