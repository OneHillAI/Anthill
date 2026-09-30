from __future__ import annotations

from ..wiki.workspace import Workspace


def warm_cache(
    ws: Workspace,
    backend,
    questions: list[str],
    *,
    new_model: str | None = None,
) -> int:
    """Re-answer a list of questions with the (new) model and overwrite the cache.

    After a model switch the semantic cache still holds answers from the old
    model. Rather than serving stale answers, this proactively regenerates the
    most-asked questions so the first real user already gets the new model's
    answer at cache speed.

    Returns the number of cache entries refreshed.
    """
    from ..cache import SemanticCache
    from .ask_shim import answer_fresh

    cache = SemanticCache(db_path=ws.root / ".cache")
    refreshed = 0
    for q in questions:
        try:
            answer = answer_fresh(ws, q, backend, model=new_model)
        except Exception:
            continue
        if answer:
            cache.store(q, answer)  # overwrites the prior entry for this prompt
            refreshed += 1
    return refreshed
