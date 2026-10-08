"""Persistent page-vector index: embed a wiki page once, and again only when its text changes.

Retrieval ranks a question against every candidate page, which needs one vector per page. Embedding
them all on every question costs one call to the embedding model per page, so the time to the first
word grew with the size of the wiki. This keeps each page's vector in a small SQLite file next to the
semantic cache (``<workspace>/.cache/page_vectors.sqlite3``), keyed by the page path (relative to the
workspace), a hash of exactly the text that is embedded, and the embedding model name. A lookup is only
a hit when all three match, so an edited page or a changed model embeds again and a stale vector is
never served. Spec: docs/specs/wiki-page-vector-index.md.

Like the semantic cache this is a disposable performance layer, never a source of truth. An index that
cannot be read, written or locked is skipped and the pages are embedded directly, which is what happened
before the index existed; a file that is plainly corrupt is deleted and rebuilt. Only the embedder's own
errors reach the caller, unchanged. Every call opens its own short-lived connection, so it is safe from
any thread. Two threads that miss on the same page may both embed it; they store the same vector.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DB_NAME = "page_vectors.sqlite3"
# Versioned like the semantic cache table: a layout change renames the table and the old rows are
# simply orphaned, never mixed in.
TABLE = "page_vectors_v1"
# How long a call waits for another writer before skipping the index. Writes are a few milliseconds.
BUSY_TIMEOUT_S = 2.0
# Keys per SELECT ... IN (...): SQLite builds before 3.32 bind at most 999 variables per statement.
_CHUNK = 400
# Freshly embedded pages are written every this many pages, not only at the end, so a warm-up that is
# interrupted (the app quits, the embedder fails) keeps the pages it already did.
SAVE_BATCH = 20

_SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    "path TEXT PRIMARY KEY, model TEXT NOT NULL, content_hash TEXT NOT NULL, "
    "dim INTEGER NOT NULL, vec BLOB NOT NULL)"
)
_UPSERT = f"INSERT OR REPLACE INTO {TABLE} (path, model, content_hash, dim, vec) VALUES (?,?,?,?,?)"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _decode(dim: object, blob: object) -> np.ndarray | None:
    """The stored vector, or None when the row does not describe a vector of the size it claims."""
    if not isinstance(dim, int) or not isinstance(blob, bytes) or dim <= 0 or len(blob) != dim * 4:
        return None
    return np.frombuffer(blob, dtype="<f4").astype(np.float32)


class PageVectorIndex:
    """The page vectors of one workspace. ``cache_dir`` is the workspace's ``.cache`` folder."""

    def __init__(self, cache_dir: Path) -> None:
        self.path = Path(cache_dir) / DB_NAME

    def vectors(
        self,
        items: Sequence[tuple[str, str]],
        embed: Callable[[str], np.ndarray],
        model: str,
        *,
        complete: bool = False,
    ) -> list[np.ndarray]:
        """One vector per ``(key, text)`` item, in order. ``key`` is the page path relative to the
        workspace and ``text`` is exactly what gets embedded. Pages whose stored hash and model match are
        read from the index; the others are embedded with ``embed`` and stored, even when ``embed`` fails
        part way (the error then propagates). ``complete`` says the items are every page of the workspace,
        so rows for other paths belong to deleted pages and are dropped."""
        if not items:
            return []
        keys = [k for k, _ in items]
        hashes = [content_hash(t) for _, t in items]
        found, stale, usable = self._lookup(keys, model, complete)

        out: list[np.ndarray | None] = [None] * len(items)
        misses = []
        for i, key in enumerate(keys):
            hit = found.get(key)
            if hit is not None and hit[0] == hashes[i]:
                out[i] = hit[1]
            else:
                misses.append(i)

        rows: list[tuple] = []
        try:
            for i in misses:
                vec = embed(items[i][1])
                out[i] = vec
                row = self._row(keys[i], model, hashes[i], vec)
                if row is not None:
                    rows.append(row)
                if usable and len(rows) >= SAVE_BATCH:
                    self._save(rows, [])
                    rows = []
        finally:
            if usable:
                self._save(rows, stale)
        return [v for v in out if v is not None]

    @staticmethod
    def _row(key: str, model: str, digest: str, vec: np.ndarray) -> tuple | None:
        arr = np.asarray(vec, dtype="<f4")
        if arr.ndim != 1 or arr.size == 0:
            return None
        return (key, model, digest, int(arr.size), arr.tobytes())

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            con.execute(_SCHEMA)
        except BaseException:
            con.close()
            raise
        return con

    def _lookup(
        self, keys: list[str], model: str, complete: bool
    ) -> tuple[dict[str, tuple[str, np.ndarray]], list[str], bool]:
        """(stored rows for ``keys`` under ``model``, paths to drop, whether the index is usable).
        Never raises: any trouble reading the index just means every page is a miss."""
        for attempt in (1, 2):
            found: dict[str, tuple[str, np.ndarray]] = {}
            stale: list[str] = []
            try:
                with closing(self._connect()) as con:
                    for i in range(0, len(keys), _CHUNK):
                        chunk = keys[i : i + _CHUNK]
                        marks = ",".join("?" * len(chunk))
                        cur = con.execute(
                            f"SELECT path, model, content_hash, dim, vec FROM {TABLE} "
                            f"WHERE path IN ({marks})",
                            chunk,
                        )
                        for path, row_model, digest, dim, blob in cur:
                            vec = _decode(dim, blob)
                            if row_model == model and vec is not None:
                                found[path] = (digest, vec)
                    if complete:
                        keep = set(keys)
                        stale = [
                            p for (p,) in con.execute(f"SELECT path FROM {TABLE}") if p not in keep
                        ]
                return found, stale, True
            except (sqlite3.Error, OSError) as e:
                # A file that is not a database, or is malformed, raises the bare DatabaseError; a
                # lock, a missing folder or a permission problem raises one of its subclasses or an
                # OSError. Only the first kind is thrown away and rebuilt.
                if attempt == 1 and type(e) is sqlite3.DatabaseError:
                    log.info("page vector index unreadable (%s), rebuilding it", e)
                    self._discard()
                    continue
                log.debug("page vector index not used: %s", e)
                break
        return {}, [], False

    def _save(self, rows: list[tuple], stale: list[str]) -> None:
        """Store the freshly embedded rows and drop the stale ones, in one transaction. Best effort."""
        if not rows and not stale:
            return
        try:
            with closing(self._connect()) as con:
                con.execute(
                    "BEGIN IMMEDIATE"
                )  # take the write lock up front, so no deadlock upgrade
                con.executemany(_UPSERT, rows)
                con.executemany(f"DELETE FROM {TABLE} WHERE path = ?", [(p,) for p in stale])
                con.execute("COMMIT")  # closing without this rolls the transaction back
        except (sqlite3.Error, OSError) as e:
            log.debug("page vector index not updated: %s", e)

    def _discard(self) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except OSError:
                pass
