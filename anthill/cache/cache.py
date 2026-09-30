from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import embedder as emb
from .guard import passes
from .store import CacheStore

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.93
# A very short query embeds imprecisely, so a loose cosine match to an unrelated cached prompt is a
# false positive - the vector behind cross-conversation cache bleed ("book the room for tomorrow"
# hitting a wind-power roadmap answer). Require a stricter similarity for short prompts.
SHORT_QUERY_TERMS = 4
SHORT_QUERY_THRESHOLD = 0.97


def _cache_disabled() -> bool:
    """Operator kill switch: ANTHILL_DISABLE_SEMANTIC_CACHE=1 skips the native lancedb/pyarrow stack
    entirely, so chat runs keyword-only."""
    return os.environ.get("ANTHILL_DISABLE_SEMANTIC_CACHE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class CacheResult:
    answer: str
    similarity: float
    cached_prompt: str
    slugs: list[str] = field(default_factory=list)  # wiki pages that grounded the cached answer


@dataclass
class SemanticCache:
    """Embed-and-lookup semantic cache with a correctness guard (§7.2-7.3).

    Usage:
        cache = SemanticCache(db_path=ws.root / ".cache")
        hit = cache.lookup("which DB did we pick?")
        if hit:
            return hit.answer
        answer = generate(...)
        cache.store("which DB did we pick?", answer)
    """

    db_path: Path
    threshold: float = DEFAULT_THRESHOLD
    _store: CacheStore | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        # The cache is a disposable performance layer: if it cannot open (lancedb import or connection
        # error, unwritable or corrupt cache dir) or the operator switched it off, degrade to a no-op
        # so chat still answers. A hard native crash (SIGSEGV) cannot be caught here; that class is
        # prevented at the source (see anthill/__init__.py) and gated by `--selfcheck-cache` at build.
        if _cache_disabled():
            return
        try:
            self.db_path.mkdir(parents=True, exist_ok=True)
            self._store = CacheStore(self.db_path)
        except Exception as e:  # never let a cache failure break the answer path
            log.warning("semantic cache unavailable, running without it: %s", e)
            self._store = None

    def _effective_threshold(self, prompt: str) -> float:
        """The similarity a match must clear. Short prompts demand a stricter floor (see above)."""
        terms = [t for t in re.split(r"\W+", (prompt or "").lower()) if len(t) > 2]
        if len(terms) < SHORT_QUERY_TERMS:
            return max(self.threshold, SHORT_QUERY_THRESHOLD)
        return self.threshold

    def lookup(self, prompt: str) -> CacheResult | None:
        """Return a cached answer if one exists and passes the correctness guard.

        No-op (returns None) when embeddings are unavailable or the store could not open - the
        semantic cache simply disables itself rather than breaking the answer path."""
        if self._store is None:
            return None
        vec = emb.safe_embed(prompt)
        if vec is None:
            return None
        rows = self._store.search(vec, self._effective_threshold(prompt))
        for row in rows:
            if passes(prompt, row.prompt):
                similarity = emb.cosine(vec, np.array(row.embedding, dtype=np.float32))
                return CacheResult(
                    answer=row.answer,
                    similarity=similarity,
                    cached_prompt=row.prompt,
                    slugs=row.slugs,
                )
        return None

    def store(self, prompt: str, answer: str, slugs: list[str] | None = None) -> None:
        if self._store is None:
            return
        vec = emb.safe_embed(prompt)
        if vec is None:  # embeddings unavailable - skip caching, don't break the caller
            return
        self._store.add(prompt, vec, answer, slugs=slugs)

    def size(self) -> int:
        return 0 if self._store is None else self._store.count()
