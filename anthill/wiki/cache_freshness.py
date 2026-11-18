"""Whether a cached answer can still be served.

A cached answer was written from the wiki as it was. It is stale when a page it cited was changed, when a
page that could have grounded it was added or changed afterwards, and it is not served when a page it cited
is no longer there. The same goes for the kept texts of the documents it was given passages of: one that is
gone or changed, or a new one that could now answer the question, settles the answer. The check costs one
file timestamp per page and per text, and a page vector only for a page that changed after the answer was
made. Page vectors come from the workspace's persistent index, so a changed page is embedded once and not
again."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import numpy as np

from ..cache import embedder as emb
from ..cache.cache import FRESH, SKIP, STALE
from ..cache.page_vectors import PageVectorIndex
from ..cache.store import CacheRow
from .doc_context import DOC_PREFIX, MIN_RARE, stored_name
from .doc_index import DocIndex
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
        cited = [s for s in row.slugs if not s.startswith(DOC_PREFIX)]  # not a page: a document
        if any(slug not in present for slug in cited):
            return SKIP
        documents = _document_verdict(spaces, row, made)
        if documents is not None:
            return documents
        question_vec = np.asarray(row.embedding, dtype=np.float32)
        for root, page in pages:
            try:
                if page.stat().st_mtime <= made:
                    continue
            except OSError:
                continue
            if page.stem in cited:
                return STALE
            if _could_ground(root, page, question_vec, relevance_floor):
                return STALE
        return FRESH

    return judge


def _document_verdict(spaces: list[Path], row: CacheRow, made: float) -> str | None:
    """The ruling on a row from the kept document texts of ``spaces``, or None when they do not change it.

    - A document the answer was given passages of whose text is gone: the answer is not served here (kept, as for
      a missing page). Whose text was changed after the answer: stale.
    - A document text that is new or changed after the answer and could now answer its question (it has at least
      ``MIN_RARE`` rare words of it): stale. A new text that is not indexed yet cannot be shown not to answer it, so
      it counts as stale too. This is what drops an "I do not know" answer once the document that answers it is
      uploaded."""
    for entry in (e for e in row.slugs if e.startswith(DOC_PREFIX)):
        path = next(
            (
                r / "text" / f"{stored_name(entry)}.txt"
                for r in spaces
                if (r / "text" / f"{stored_name(entry)}.txt").is_file()
            ),
            None,
        )
        if path is None:
            return SKIP
        try:
            if path.stat().st_mtime > made:
                return STALE
        except OSError:
            return SKIP
    for root in spaces:
        try:
            newer = {
                p.name.removesuffix(".txt")
                for p in (root / "text").glob("*.txt")
                if p.stat().st_mtime > made
            }
        except OSError:
            continue
        if not newer:
            continue
        index = DocIndex(root / ".cache", root / "text")
        if not index.exists() or not newer <= set(index.docs()):
            return STALE
        if any(h.doc in newer for h in index.search(row.prompt, 8, min_rare=MIN_RARE)):
            return STALE
    return None


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
