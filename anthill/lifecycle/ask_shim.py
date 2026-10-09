from __future__ import annotations

from ..wiki import prompts
from ..wiki.workspace import Workspace


def answer_fresh(ws: Workspace, question: str, backend, *, model: str | None = None) -> str:
    """Generate a wiki-grounded answer bypassing the cache.

    Used by cache warm-up to regenerate answers with a new model. Mirrors the
    retrieval + prompt path of wiki.ask.ask but never reads or skips the cache.
    """
    from ..inference.ollama import OllamaBackend
    from ..wiki.ask import _relevant_pages

    pages = _relevant_pages(ws, question, 3)
    context = "\n\n---\n\n".join(p.read_text() for p in pages) if pages else "(the wiki is empty)"
    from ..inference.context import known_window

    msgs = prompts.answer_question(context, question, window=known_window(backend, model))
    if model and isinstance(backend, OllamaBackend):
        return backend.chat(msgs, model=model)
    return backend.chat(msgs)
