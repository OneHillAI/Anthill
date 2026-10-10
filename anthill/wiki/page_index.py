"""Keep the page-vector index current BEFORE a question is asked.

``anthill.cache.page_vectors`` stores one vector per wiki page, so a question only embeds pages that are new
or changed. Left lazy, the first question after an upgrade (or after a batch of new pages) would pay for all
of them. This module builds the index ahead of time instead: once in the background when the app starts, and
for each page as it is saved. A wiki with no pages costs nothing, and a very small one costs well under a
second when Ollama and the embedding model are already up. Everything here is best effort: it never raises,
never blocks a save, and waits a bounded time for the embedding model to come up before giving up. The question path still embeds any page the index lacks, so a page this
module missed is only slower, never wrong. Spec: docs/specs/wiki-page-vector-index.md.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path

from ..cache import embedder as emb
from ..cache.page_vectors import PageVectorIndex
from ..common.text import strip_frontmatter

log = logging.getLogger(__name__)

# How much of a page is embedded. Ranking in ``ask.py`` uses the same text, so a vector made here is the
# vector a question looks up.
EMBED_CHARS = 2000
# Set ANTHILL_WARM_PAGE_INDEX=0 to turn the background building off (the test suite does).
ENABLED = os.environ.get("ANTHILL_WARM_PAGE_INDEX", "1") != "0"
# Seconds the startup pass waits so the app and the embedding model can come up first.
STARTUP_DELAY_S = 3.0
# When the embedding model is not ready yet (a slow boot, or its first download), the startup pass waits and
# tries again after each of these many seconds, about thirteen minutes in all, then stops. The question path
# still embeds whatever is missing, so giving up only means the first question pays for it.
RETRY_WAITS_S = (15, 30, 60, 120, 240, 300)
# A saved page that finds the model not ready retries after these waits before it is dropped.
PAGE_RETRY_WAITS_S = (15, 30, 60, 120)


def embed_text(path: Path) -> str:
    """Exactly the text that is embedded for a page: its body after the frontmatter, first 2000 characters."""
    return strip_frontmatter(path.read_text())[:EMBED_CHARS]


def workspace_roots() -> list[Path]:
    """Every wiki workspace on this install that has a ``wiki`` folder: the organisation wiki, the legacy
    single-node personal wiki, and each per-user and per-team wiki (the layout ``workspace_for`` decides)."""
    org = Path(os.environ.get("ANTHILL_ORG_WIKI", "data/org-wiki"))
    legacy = Path(os.environ.get("ANTHILL_WORKSPACE", "workspace"))
    wikis = Path(os.environ.get("ANTHILL_WIKI_ROOT", "data/wikis"))
    from .workspace import org_wikis_base

    candidates = [
        org,
        *sorted(org_wikis_base().glob("org-*")),
        legacy,
        *sorted(wikis.glob("user-*")),
        *sorted(wikis.glob("team-*")),
    ]
    roots: list[Path] = []
    seen: set[Path] = set()
    for r in candidates:
        try:
            key = r.resolve()
        except OSError:
            continue
        if key not in seen and (r / "wiki").is_dir():
            seen.add(key)
            roots.append(r)
    return roots


def warm_workspace(root: Path) -> int:
    """Embed the pages of the workspace at ``root`` that the index lacks or that changed, and drop rows of
    pages that are gone. Returns how many pages were embedded. A workspace with no pages does nothing at all
    (no index file is created). Never raises."""
    made = 0
    try:
        pages = sorted((root / "wiki").glob("*.md"))
        if not pages:
            return 0
        items = []
        for p in pages:
            try:
                items.append((p.relative_to(root).as_posix(), embed_text(p)))
            except (
                OSError,
                ValueError,
            ) as e:  # one unreadable page (a bad encoding, say) is skipped
                log.debug("page index skips unreadable page %s: %s", p, e)

        def counting(text: str):
            nonlocal made
            vec = emb.embed(text)
            made += 1
            return vec

        # complete=True drops rows of pages that are gone. When a page was skipped as unreadable, its row is
        # kept (the read failure may be transient, such as a file held open by a sync client).
        PageVectorIndex(root / ".cache").vectors(
            items, counting, emb.MODEL_NAME, complete=len(items) == len(pages)
        )
    except Exception as e:  # an embedder or file problem must never reach the caller
        log.debug("page index warm-up stopped for %s: %s", root, e)
    return made


def _model_ready() -> bool:
    try:
        return bool(emb.available())
    except Exception:
        return False


def warm_all(
    delay: float | None = None,
    *,
    sleep=time.sleep,
    waits: tuple[float, ...] = RETRY_WAITS_S,
) -> int:
    """The startup pass: warm every workspace in the background, waiting (a bounded time) for the embedding
    model if it is not ready yet. Returns pages embedded in total."""
    if not ENABLED:
        return 0
    sleep(STARTUP_DELAY_S if delay is None else delay)
    for wait in (0, *waits):
        sleep(wait)
        if _model_ready():
            return sum(warm_workspace(r) for r in workspace_roots())
    return 0


# One background worker embeds saved pages one at a time, so a bulk import queues instead of fanning out.
_pending: queue.Queue[tuple[Path, Path]] = queue.Queue(maxsize=1000)
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None
# True once a saved page has waited out its retries without the model coming up (see warm_page_with_retry).
_gave_up = False


def warm_page(root: Path, path: Path) -> bool:
    """Embed one saved page into its workspace's index. True when the index now holds it. Never raises."""
    try:
        if not emb.available() or not path.is_file():
            return False
        PageVectorIndex(root / ".cache").vectors(
            [(path.relative_to(root).as_posix(), embed_text(path))],
            emb.embed,
            emb.MODEL_NAME,
        )
        return True
    except Exception as e:
        log.debug("page index not updated for %s: %s", path, e)
        return False


def warm_page_with_retry(
    root: Path,
    path: Path,
    *,
    sleep=time.sleep,
    waits: tuple[float, ...] = PAGE_RETRY_WAITS_S,
) -> bool:
    """``warm_page``, waiting (a bounded time) for the embedding model when it is not ready yet, so a page
    saved while the model is still downloading is not lost. Once one page has used up its waits, later pages
    check once without waiting until the model is seen ready again, so an install that never gets the model
    (no Ollama, no bge-m3) does not sleep through every saved page."""
    global _gave_up
    if _gave_up:
        if not _model_ready():
            return False
        _gave_up = False
        return warm_page(root, path)
    for wait in (0, *waits):
        sleep(wait)
        if _model_ready():
            return warm_page(root, path)
    _gave_up = True
    return False


def _run_worker(q: queue.Queue | None = None, *, once: bool = False) -> None:
    """The background worker's loop: take a saved page, embed it. ``q`` and ``once`` exist for tests."""
    source = q if q is not None else _pending
    while True:
        root, path = source.get()
        warm_page_with_retry(root, path)
        if once:
            return


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, daemon=True, name="anthill-page-index")
            _worker.start()


def warm_page_soon(root: Path, path: Path) -> None:
    """Queue a just-saved page for the background worker. Returns at once and never raises."""
    if not ENABLED:
        return
    try:
        _pending.put_nowait((root, path))
        _ensure_worker()
    except queue.Full:
        log.debug(
            "page index queue is full, %s will be embedded by the question that needs it", path
        )
    except Exception as e:
        log.debug("could not queue %s for the page index: %s", path, e)
