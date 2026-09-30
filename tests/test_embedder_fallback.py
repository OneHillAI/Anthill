"""Graceful degradation when the embedding stack (Ollama's bge-m3 model) is unavailable - Ollama not
running, or the model not pulled yet on a fresh install. Chat must still answer (model + keyword
retrieval); the semantic cache disables itself instead of crashing.

Regression for the original shipped-dmg break, when embeddings ran via sentence-transformers (a
heavy dependency the packaged app excludes) and chat returned `No module named
'sentence_transformers'` outright. embedder.py has since moved to Ollama's already-bundled runtime
(see docs/specs/ollama-served-embeddings.md), but the same never-crash contract - and this test file
- still applies to ITS failure modes (Ollama unreachable, model not pulled).
"""

from anthill.cache import embedder as emb
from anthill.cache.cache import SemanticCache
from anthill.wiki.ask import ask
from anthill.wiki.workspace import Workspace


def test_safe_embed_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(emb, "available", lambda: False)
    assert emb.safe_embed("hello") is None


def test_safe_embed_none_on_load_failure(monkeypatch):
    """available() can be True but the model still fails to load (e.g. offline first run)."""
    monkeypatch.setattr(emb, "available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("model cannot load")

    monkeypatch.setattr(emb, "embed", _boom)
    assert emb.safe_embed("hello") is None


def test_cache_noops_without_embeddings(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "available", lambda: False)
    cache = SemanticCache(db_path=tmp_path / ".cache")
    cache.store("which db did we pick?", "postgres")  # must not raise
    assert cache.lookup("which db did we pick?") is None  # disabled, not crashing
    assert cache.size() == 0  # nothing was stored


def test_chat_answers_without_embeddings(tmp_path, monkeypatch):
    """The regression: with embeddings fully unavailable, ask() still returns a model answer
    (via keyword retrieval) instead of dead-ending on the embedder import."""
    monkeypatch.setattr(emb, "available", lambda: False)

    def _boom(*a, **k):
        raise RuntimeError("Ollama unreachable")

    monkeypatch.setattr(emb, "embed", _boom)

    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("policy", "# Policy\n\nWe keep all data local.\n")
    ws.rebuild_index()

    class _Backend:
        def chat(self, messages, **k):
            return "local answer"

    answer, _slugs, cache_hit = ask(ws, "what is our data policy?", _Backend())
    assert answer == "local answer"
    assert cache_hit is False
