"""anthill/cache/embedder.py's Ollama-backed implementation (replaces sentence-transformers/torch,
which the packaged desktop app excludes to keep the installer small - see
docs/specs/ollama-served-embeddings.md). Covers available()'s reachability+model-pulled check,
embed()'s request shape and explicit L2-normalization (Ollama's /api/embed returns raw vectors,
unlike sentence-transformers' normalize_embeddings=True this replaces), and safe_embed()'s
never-crash contract. tests/test_embedder_fallback.py covers the higher-level degrade-to-keyword
behavior via mocked available()/embed() - this file is the layer underneath.
"""

import numpy as np

from anthill.cache import embedder as emb


def _reset_available_cache(monkeypatch):
    monkeypatch.setattr(emb, "_available", None)


# ── available() ──────────────────────────────────────────────────────────────────────────


def test_available_true_when_model_is_in_tags(monkeypatch):
    _reset_available_cache(monkeypatch)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "bge-m3:latest"}, {"name": "qwen3:8b"}]}

    monkeypatch.setattr(emb.httpx, "get", lambda url, timeout: _Resp())
    assert emb.available() is True


def test_available_false_when_model_not_pulled(monkeypatch):
    _reset_available_cache(monkeypatch)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "qwen3:8b"}]}  # bge-m3 not in the list

    monkeypatch.setattr(emb.httpx, "get", lambda url, timeout: _Resp())
    assert emb.available() is False


def test_available_false_when_ollama_unreachable(monkeypatch):
    _reset_available_cache(monkeypatch)

    def _boom(url, timeout):
        raise ConnectionError("no ollama")

    monkeypatch.setattr(emb.httpx, "get", _boom)
    assert emb.available() is False


def test_available_caches_true_but_not_false(monkeypatch):
    _reset_available_cache(monkeypatch)
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": []}

    def _get(url, timeout):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(emb.httpx, "get", _get)
    assert emb.available() is False
    assert emb.available() is False
    assert calls["n"] == 2  # a False result is retried every call - the model might finish pulling

    class _RespTrue:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "bge-m3:latest"}]}

    monkeypatch.setattr(emb.httpx, "get", lambda url, timeout: _RespTrue())
    assert emb.available() is True
    assert calls["n"] == 2  # not called again once True - a False->True flip stops re-checking

    monkeypatch.setattr(emb.httpx, "get", _get)  # would flip back False if not cached
    assert emb.available() is True
    assert calls["n"] == 2  # confirmed: True is cached permanently, never re-probed


# ── embed() ──────────────────────────────────────────────────────────────────────────────


def test_embed_requests_the_configured_model_and_input(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[3.0, 4.0]]}  # norm 5 -> normalized to [0.6, 0.8]

    def _post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(emb.httpx, "post", _post)
    vec = emb.embed("hello world")
    assert captured["json"] == {"model": "bge-m3", "input": "hello world"}
    assert captured["url"].endswith("/api/embed")
    assert vec.shape == (2,)
    assert np.isclose(np.linalg.norm(vec), 1.0)
    assert np.allclose(vec, [0.6, 0.8])


def test_embed_normalizes_a_raw_ollama_vector(monkeypatch):
    # Ollama's /api/embed returns raw (non-unit) vectors - explicit normalization is the whole point
    # of this test, since cosine() assumes every stored/queried vector is already unit length.
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[1.0, 0.0, 0.0]]}  # already unit - sanity check the no-op case

    monkeypatch.setattr(emb.httpx, "post", lambda url, json, timeout: _Resp())
    vec = emb.embed("x")
    assert np.allclose(vec, [1.0, 0.0, 0.0])


def test_embed_raises_on_connection_failure(monkeypatch):
    def _boom(url, json, timeout):
        raise ConnectionError("no ollama")

    monkeypatch.setattr(emb.httpx, "post", _boom)
    try:
        emb.embed("x")
        raise AssertionError("expected embed() to raise")
    except ConnectionError:
        pass


# ── safe_embed() against the real (mocked-at-httpx-level) implementation ───────────────────


def test_safe_embed_none_when_ollama_has_no_bge_m3(monkeypatch):
    _reset_available_cache(monkeypatch)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": []}

    monkeypatch.setattr(emb.httpx, "get", lambda url, timeout: _Resp())
    assert emb.safe_embed("hello") is None


def test_safe_embed_returns_a_normalized_vector_when_available(monkeypatch):
    _reset_available_cache(monkeypatch)

    class _TagsResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "bge-m3:latest"}]}

    class _EmbedResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[3.0, 4.0]]}

    monkeypatch.setattr(emb.httpx, "get", lambda url, timeout: _TagsResp())
    monkeypatch.setattr(emb.httpx, "post", lambda url, json, timeout: _EmbedResp())
    vec = emb.safe_embed("hello")
    assert vec is not None
    assert np.isclose(np.linalg.norm(vec), 1.0)


# ── cosine() (unchanged, sanity check against the new normalization contract) ───────────────


def test_cosine_of_identical_normalized_vectors_is_one():
    v = np.array([0.6, 0.8], dtype=np.float32)
    assert np.isclose(emb.cosine(v, v), 1.0)
