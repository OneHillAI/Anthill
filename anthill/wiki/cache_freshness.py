"""Whether a cached answer can still be served.

A cached answer was written from the wiki as it was. It is stale when a page it cited was changed, when a
page that could have grounded it was added or changed afterwards, and it is not served when a page it cited
is no longer there. The check costs one file timestamp per page, and a page vector only for a page that
changed after the answer was made. Page vectors come from the workspace's persistent index, so a changed
page is embedded once and not again."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import numpy as np

from ..cache import embedder as emb
from ..cache.cache import FRESH, SKIP, STALE
from ..cache.page_vectors import PageVectorIndex
from ..cache.store import CacheRow
from .page_index import embed_text


def judge_for(roots: Iterable[Path], relevance_floor: float) -> Callable[[CacheRow], str]:
    """A judge for ``SemanticCache.lookup``: rules on a row against the wiki workspaces ``roots`` that ground
    the current chat. ``relevance_floor`` is the similarity at which a page counts as relevant to a question
    (the grounding floor retrieval itself uses)."""
    spaces = [Path(r) for r in roots]

    def judge(row: CacheRow) -> str:
        try:
            made = datetime.fromisoformat(row.created_at).timestamp()
        except ValueError:
            return STALE  # no usable time: it cannot be shown to be current
        pages = [(root, p) for root in spaces for p in sorted((root / "wiki").glob("*.md"))]
        present = {p.stem for _root, p in pages}
        # A cited page that is not in these workspaces: deleted, or outside the scope chosen for this
        # chat. Either way this answer is not served here, and it is kept for a chat where it still holds.
        if any(slug not in present for slug in row.slugs):
            return SKIP
        question_vec = np.asarray(row.embedding, dtype=np.float32)
        for root, page in pages:
            try:
                if page.stat().st_mtime <= made:
                    continue
            except OSError:
                continue
            if page.stem in row.slugs:
                return STALE
            if _could_ground(root, page, question_vec, relevance_floor):
                return STALE
        return FRESH

    return judge


def _could_ground(root: Path, page: Path, question_vec: np.ndarray, floor: float) -> bool:
    """True when this page is close enough to the cached question that retrieval could now use it. A page
    whose vector cannot be had counts as relevant: dropping an answer costs one fresh answer."""
    try:
        vec = PageVectorIndex(root / ".cache").vectors(
            [(page.relative_to(root).as_posix(), embed_text(page))], emb.embed, emb.MODEL_NAME
        )[0]
    except Exception:
        return True
    return emb.cosine(question_vec, vec) >= floor
