"""An index of the text Anthill kept from each uploaded document, so a question can be matched against the
document itself and not only against its summary page. Spec: docs/specs/index-extracted-text.md, issue #226.

Each kept text (``<workspace>/text/<original's name>.txt``, see ``ingest.keep_extracted_text``) is split into
passages of about 800 characters packed from whole paragraphs. Every passage keeps where it came from: its
character and byte offsets in the text file, the page number for a PDF (counted from the ``---`` page separators
the reader writes) and the heading above it. The words of the passages go into a SQLite full-text index next to
the page vectors (``<workspace>/.cache/doc_passages.sqlite3``), keyed by the text's name and a hash of its
content: an unchanged text is not indexed again, a changed one replaces its passages, and a text that is gone
drops them.

The index holds positions and words, not the text: a hit is read back from the text file by its byte offsets and
checked against a CRC kept with it, so the file stays the one copy of the text and a stale entry is dropped
instead of returning the wrong words. Ranking is BM25 inside SQLite. Nothing here calls a model, so indexing a
text takes milliseconds to a second and a search about a millisecond, and nothing competes with chat for the
model. Like the semantic cache and the page-vector index it is a disposable performance layer, never a source of
truth: an index that cannot be opened, read or written (or an SQLite without contentless full-text tables, which
needs 3.43) is skipped, a plainly corrupt file is deleted and rebuilt, and nothing in this module raises to its
caller. Nothing uses it for answers yet.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import os
import queue
import re
import sqlite3
import threading
import unicodedata
import zlib
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

ENABLED = os.environ.get("ANTHILL_INDEX_DOCS", "1") != "0"
DB_NAME = "doc_passages.sqlite3"
# Versioned like the other derived stores: a layout change renames the tables and the old rows are orphaned.
DOCS_TABLE = "docs_v1"
PASSAGES_TABLE = "passages_v1"
WORDS_TABLE = "words_v1"
PASSAGE_CHARS = 800  # the size a passage is packed up to (about 200 tokens), as in wiki/passages.py
MAX_QUERY_TERMS = 12
_MAX_EXCLUDED = 400  # documents named in one query; any more are filtered after it
# A query word is "rare" when it is in at most this share of the indexed passages (but at least one passage,
# and at most RARE_CAP passages). Rare words are what tell a passage about the question from one that only shares
# common words with it, and unlike a BM25 score the count means the same in a small wiki and a large one.
RARE_FRACTION = 0.25
RARE_CAP = 50
BUSY_TIMEOUT_S = 2.0

_PAGE_BREAK = "\n\n---\n\n"  # what the reader writes between the pages of a PDF
_BLANK_LINE = re.compile(r"\n[ \t\r]*\n+")
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_WORD = re.compile(r"\w{2,}")

_ENCODING = "utf-8"
# A text is read with its bytes kept exactly (an invalid byte becomes one lone surrogate and encodes back to the
# same byte), so a passage's byte offsets always lead back to the same bytes in the file.
_ESCAPE = "surrogateescape"

_SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS {DOCS_TABLE} ("
    "doc TEXT PRIMARY KEY, text_hash TEXT NOT NULL, chars INTEGER NOT NULL, passages INTEGER NOT NULL)",
    f"CREATE TABLE IF NOT EXISTS {PASSAGES_TABLE} ("
    "id INTEGER PRIMARY KEY, doc TEXT NOT NULL, n INTEGER NOT NULL, start INTEGER NOT NULL, "
    "stop INTEGER NOT NULL, bstart INTEGER NOT NULL, bstop INTEGER NOT NULL, crc INTEGER NOT NULL, "
    "page INTEGER, heading TEXT NOT NULL)",
    f"CREATE INDEX IF NOT EXISTS {PASSAGES_TABLE}_doc ON {PASSAGES_TABLE} (doc)",
    # Contentless: it keeps the words and ranks them, not the text. contentless_delete (SQLite 3.43) lets
    # a text's rows be removed by rowid.
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {WORDS_TABLE} USING fts5("
    "text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2')",
)


class Passage(NamedTuple):
    n: int  # the passage's number in its text, from 0
    start: int  # character offsets in the text file: ``text[start:stop]`` is the passage
    stop: int
    page: int | None  # the PDF page it starts on, else None
    heading: str  # the Markdown heading above it ("" when there is none)
    text: str


class Hit(NamedTuple):
    doc: str  # the original's name, as in the Files tab
    n: int
    start: int  # character offsets in the text file
    stop: int
    page: int | None
    heading: str
    text: str  # read back from the text file by the passage's byte offsets
    score: float  # BM25: lower is a better match
    rare: int = (
        0  # how many rare words of the query the passage contains (see ``search``'s ``min_rare``)
    )


# ── splitting ─────────────────────────────────────────────────────────────────


def _paragraphs(text: str) -> list[tuple[int, int]]:
    """The (start, stop) of each paragraph: the runs of text between blank lines, trimmed."""
    spans = []
    cursor = 0
    for m in (*_BLANK_LINE.finditer(text), None):
        end = m.start() if m else len(text)
        seg = text[cursor:end]
        lead = len(seg) - len(seg.lstrip())
        body = seg.strip()
        if body:
            spans.append((cursor + lead, cursor + lead + len(body)))
        cursor = m.end() if m else len(text)
    return spans


def _cut_long(text: str, start: int, stop: int) -> list[tuple[int, int]]:
    """Cut one paragraph longer than a passage into pieces of at most ``PASSAGE_CHARS``, at a line end, a
    sentence end or a space when there is one in the second half of the piece, else in the middle of a word."""
    pieces = []
    while stop - start > PASSAGE_CHARS:
        window = text[start : start + PASSAGE_CHARS]
        cut = max(window.rfind("\n"), window.rfind(". ") + 1, window.rfind(" "))
        cut = cut if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS
        piece = text[start : start + cut]
        body = piece.strip()
        if body:
            lead = len(piece) - len(piece.lstrip())
            pieces.append((start + lead, start + lead + len(body)))
        start += cut
        start += len(text[start:stop]) - len(text[start:stop].lstrip())
    if stop > start and text[start:stop].strip():
        pieces.append((start, stop))
    return pieces


def split_document(text: str, *, pdf: bool = False) -> list[Passage]:
    """Split a kept text into passages of about ``PASSAGE_CHARS``, packed from whole paragraphs, each with its
    offsets, its PDF page (when ``pdf``) and the heading above it. ``text[p.start:p.stop] == p.text`` for every
    passage, so a passage can always be read back from the file. The page-break lines of a PDF are not text."""
    breaks = [m.start() for m in re.finditer(re.escape(_PAGE_BREAK), text)] if pdf else []
    heads = [(m.start(), m.group(1)[:120]) for m in _HEADING.finditer(text)]
    head_at = [h[0] for h in heads]

    paras: list[tuple[int, int]] = []
    for start, stop in _paragraphs(text):
        if pdf and text[start:stop] == "---":
            continue
        if stop - start > PASSAGE_CHARS:
            paras.extend(_cut_long(text, start, stop))
        else:
            paras.append((start, stop))

    spans: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    for start, stop in paras:
        if (
            cur is not None
            and stop - cur[0] <= PASSAGE_CHARS
            and (not pdf or not _crosses(breaks, cur[0], start))
        ):
            cur = (cur[0], stop)
            continue
        if cur is not None:
            spans.append(cur)
        cur = (start, stop)
    if cur is not None:
        spans.append(cur)

    out = []
    for n, (start, stop) in enumerate(spans):
        i = bisect.bisect_right(head_at, start) - 1
        out.append(
            Passage(
                n=n,
                start=start,
                stop=stop,
                page=bisect.bisect_right(breaks, start) + 1 if pdf else None,
                heading=heads[i][1] if i >= 0 else "",
                text=text[start:stop],
            )
        )
    return out


def _crosses(breaks: list[int], a: int, b: int) -> bool:
    """True when a PDF page break lies between offsets ``a`` and ``b``: one passage never spans two pages."""
    return bisect.bisect_right(breaks, a) != bisect.bisect_right(breaks, b)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


# ── the index ─────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    """Lowercase without accents, the way the index's tokenizer compares words."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )


def _fts_text(passage: str) -> str:
    """The passage as text SQLite can bind: a lone surrogate (an invalid byte of the file) becomes U+FFFD."""
    return passage.encode(_ENCODING, "replace").decode(_ENCODING)


class DocIndex:
    """The passage index of one workspace. ``cache_dir`` is the workspace's ``.cache`` folder; the kept texts
    it points into are in ``text_dir`` (by default the workspace's ``text`` folder, beside it)."""

    def __init__(self, cache_dir: Path, text_dir: Path | None = None) -> None:
        self.path = Path(cache_dir) / DB_NAME
        self.text_dir = Path(text_dir) if text_dir is not None else Path(cache_dir).parent / "text"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        for stmt in _SCHEMA:
            con.execute(stmt)
        return con

    def _run(self, work, default):
        """Run ``work(connection)``. A corrupt file is deleted and the work tried once more; any other
        failure, or an SQLite without contentless full-text tables, returns ``default``."""
        for attempt in (1, 2):
            try:
                with closing(self._connect()) as con:
                    result = work(con)
                    con.commit()
                    return result
            except sqlite3.OperationalError as e:
                log.debug("doc index unavailable (%s): %s", self.path, e)
                return default
            except sqlite3.DatabaseError as e:  # "file is not a database", "malformed"
                log.warning("doc index %s is damaged, rebuilding it: %s", self.path, e)
                self.path.unlink(missing_ok=True)
                if attempt == 2:
                    return default
            except OSError as e:
                log.debug("doc index cannot be opened (%s): %s", self.path, e)
                return default
        return default

    def docs(self) -> dict[str, str]:
        """The indexed texts: name -> content hash."""
        return self._run(
            lambda con: dict(con.execute(f"SELECT doc, text_hash FROM {DOCS_TABLE}").fetchall()),  # nosec B608
            {},
        )

    @staticmethod
    def _delete_rows(con: sqlite3.Connection, doc: str) -> None:
        # The table names are module constants, not input.
        con.execute(  # nosec B608
            f"DELETE FROM {WORDS_TABLE} WHERE rowid IN (SELECT id FROM {PASSAGES_TABLE} WHERE doc = ?)",
            (doc,),
        )
        con.execute(f"DELETE FROM {PASSAGES_TABLE} WHERE doc = ?", (doc,))  # nosec B608

    def index(self, doc: str, text: str, *, pdf: bool = False) -> int:
        """Index the text of ``doc`` (the content of its text file), replacing what was indexed for it.
        Returns the passages written, or 0 when the text is unchanged since it was last indexed."""
        digest = text_hash(text)

        def work(con: sqlite3.Connection) -> int:
            row = con.execute(  # nosec B608
                f"SELECT text_hash FROM {DOCS_TABLE} WHERE doc = ?", (doc,)
            ).fetchone()
            if row is not None and row[0] == digest:
                return 0
            passages = split_document(text, pdf=pdf)  # only a new or changed text is split
            self._delete_rows(con, doc)
            char_pos = byte_pos = 0
            for p in passages:
                byte_pos += len(text[char_pos : p.start].encode(_ENCODING, _ESCAPE))
                raw = p.text.encode(_ENCODING, _ESCAPE)
                cur = con.execute(  # nosec B608
                    f"INSERT INTO {PASSAGES_TABLE} (doc, n, start, stop, bstart, bstop, crc, page, heading) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        doc,
                        p.n,
                        p.start,
                        p.stop,
                        byte_pos,
                        byte_pos + len(raw),
                        zlib.crc32(raw),
                        p.page,
                        p.heading,
                    ),
                )
                con.execute(  # nosec B608
                    f"INSERT INTO {WORDS_TABLE} (rowid, text) VALUES (?, ?)",
                    (cur.lastrowid, _fts_text(p.text)),
                )
                byte_pos += len(raw)
                char_pos = p.stop
            con.execute(  # nosec B608
                f"INSERT OR REPLACE INTO {DOCS_TABLE} (doc, text_hash, chars, passages) VALUES (?,?,?,?)",
                (doc, digest, len(text), len(passages)),
            )
            return len(passages)

        return self._run(work, 0)

    def drop(self, doc: str) -> None:
        def work(con: sqlite3.Connection) -> None:
            self._delete_rows(con, doc)
            con.execute(f"DELETE FROM {DOCS_TABLE} WHERE doc = ?", (doc,))  # nosec B608

        self._run(work, None)

    def exists(self) -> bool:
        """True when this workspace has an index file. Searching one that has none would create it."""
        return self.path.is_file()

    def _rare_terms(self, terms: list[str]) -> set[str]:
        """The words of ``terms`` that are rare in this index (see ``RARE_FRACTION``)."""

        def work(con: sqlite3.Connection) -> set[str]:
            total = con.execute(f"SELECT count(*) FROM {PASSAGES_TABLE}").fetchone()[0]  # nosec B608
            limit = max(1, min(int(RARE_FRACTION * total), RARE_CAP))
            rare = set()
            for t in terms:
                n = con.execute(  # nosec B608
                    f"SELECT count(*) FROM {WORDS_TABLE} WHERE {WORDS_TABLE} MATCH ?", (f'"{t}"',)
                ).fetchone()[0]
                if 0 < n <= limit:
                    rare.add(t)
            return rare

        return self._run(work, set())

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        min_rare: int = 0,
        exclude: frozenset[str] = frozenset(),
    ) -> list[Hit]:
        """The best passages for ``query``, best first, by BM25 over the words of the query (any of them). The
        text of each is read from its text file; a hit whose file is gone or no longer matches what was
        indexed (the CRC differs) is left out, which is a stale entry the next sync replaces.

        ``exclude`` names documents (by stored name) that must not be returned: the ones an answer may not read.
        They are left out in the query, so a hidden document never takes the place of a visible one.

        With ``min_rare`` a passage must also contain at least that many distinct rare words of the query
        (``Hit.rare`` says how many), and the hits are ordered by that count, then by BM25. A passage that only
        shares common words with the question ("the", "what", "is") is then not a hit."""
        terms = list(dict.fromkeys(_WORD.findall((query or "").lower())))[:MAX_QUERY_TERMS]
        if not terms or limit < 1:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        if min_rare > 0:
            rare = self._rare_terms(terms)
            if len(rare) < min_rare:
                return []
            candidates = max(limit * 4, 8)
        else:
            rare, candidates = set(), limit

        skip = sorted(exclude)[:_MAX_EXCLUDED]  # SQLite allows at most 999 variables in one query
        marks = ",".join("?" * len(skip))
        where_not = f" AND p.doc NOT IN ({marks})" if skip else ""

        def work(con: sqlite3.Connection) -> list[tuple]:
            return con.execute(  # nosec B608
                "SELECT p.doc, p.n, p.start, p.stop, p.bstart, p.bstop, p.crc, p.page, p.heading, "
                f"bm25({WORDS_TABLE}) AS score FROM {WORDS_TABLE} "
                f"JOIN {PASSAGES_TABLE} p ON p.id = {WORDS_TABLE}.rowid "
                f"WHERE {WORDS_TABLE} MATCH ?{where_not} ORDER BY score LIMIT ?",
                (match, *skip, candidates),
            ).fetchall()

        folded_rare = {_fold(t) for t in rare}
        hits = []
        for doc, n, start, stop, bstart, bstop, crc, page, heading, score in self._run(work, []):
            if doc in exclude:
                continue  # more hidden documents than the query could name
            text = self._read(doc, bstart, bstop, crc)
            if text is None:
                continue
            found = len(folded_rare & set(_WORD.findall(_fold(text)))) if rare else 0
            if found >= min_rare:
                hits.append(Hit(doc, n, start, stop, page, heading, text, score, found))
        if min_rare > 0:
            hits.sort(key=lambda h: (-h.rare, h.score))
        return hits[:limit]

    def _read(self, doc: str, bstart: int, bstop: int, crc: int) -> str | None:
        """The passage at these byte offsets of the text file of ``doc``, or None when the file is gone or its
        bytes there are not the ones that were indexed."""
        try:
            with (self.text_dir / f"{doc}.txt").open("rb") as f:
                f.seek(bstart)
                raw = f.read(bstop - bstart)
        except OSError:
            return None
        if len(raw) != bstop - bstart or zlib.crc32(raw) != crc:
            return None
        return raw.decode(_ENCODING, "replace")


# ── whether this SQLite can run it ────────────────────────────────────────────

_support: tuple[bool, str] | None = None


def support() -> tuple[bool, str]:
    """(True, "") when this SQLite can run the index, else (False, why). It tries the one statement the index
    needs, a contentless full-text table with ``contentless_delete`` (SQLite 3.43 with FTS5), in a memory
    database, so the answer is for the SQLite this process really uses, a packaged app's included."""
    global _support
    if _support is None:
        try:
            with closing(sqlite3.connect(":memory:")) as con:
                con.execute(
                    "CREATE VIRTUAL TABLE probe USING fts5(text, content='', contentless_delete=1)"
                )
            _support = (True, "")
        except sqlite3.Error as e:
            _support = (
                False,
                f"this SQLite ({sqlite3.sqlite_version}) cannot build it; it needs 3.43 with FTS5 ({e})",
            )
    return _support


def off_notice() -> str:
    """What to tell a person when document search is off, or "" when it is on. The raw reason is for the log."""
    ok, _why = support()
    if ok:
        return ""
    return (
        "Document search is off on this install, so answers use the AI's summary pages only. The database engine "
        f"built into this copy of Anthill (SQLite {sqlite3.sqlite_version}) is too old, or built without a feature "
        "it needs: document search needs SQLite 3.43 or newer with full-text search. Updating Anthill fixes it; "
        "if you run from source, use a Python with a newer SQLite."
    )


# ── keeping it up to date ─────────────────────────────────────────────────────


def _is_pdf(doc: str) -> bool:
    return doc.lower().endswith(".pdf")


def index_text_file(root: Path, path: Path) -> int:
    """Index one kept text of the workspace at ``root``. Returns the passages written. Never raises."""
    try:
        doc = path.name.removesuffix(".txt")
        text = path.read_bytes().decode(_ENCODING, _ESCAPE)
        return DocIndex(root / ".cache", path.parent).index(doc, text, pdf=_is_pdf(doc))
    except Exception as e:
        log.debug("doc index skips %s: %s", path, e)
        return 0


def sync_workspace(root: Path) -> int:
    """Bring the index of the workspace at ``root`` in line with its kept texts: index the ones that are new
    or changed and drop the ones that are gone. Returns the passages written. A workspace with no kept text and
    no index does nothing at all (no index file is created). Never raises."""
    written = 0
    try:
        texts = {p.name.removesuffix(".txt"): p for p in sorted((root / "text").glob("*.txt"))}
        index = DocIndex(root / ".cache")
        if not texts and not index.path.exists():
            return 0
        known = index.docs()
        for path in texts.values():
            written += index_text_file(root, path)
        for doc in set(known) - set(texts):
            index.drop(doc)
    except Exception as e:
        log.debug("doc index sync stopped for %s: %s", root, e)
    return written


def sync_all() -> int:
    """The startup pass: bring every workspace's index in line with its kept texts, which also indexes the
    texts kept before the index existed. Returns the passages written in total. Never raises."""
    if not ENABLED:
        return 0
    ok, why = support()
    if not ok:
        log.warning("document search is off: %s", why)  # a warning: nothing else says so
        return 0
    from .page_index import workspace_roots

    try:
        return sum(sync_workspace(r) for r in workspace_roots())
    except Exception as e:
        log.debug("doc index startup pass stopped: %s", e)
        return 0


# One background worker indexes saved texts one at a time, so a bulk upload queues instead of fanning out.
_pending: queue.Queue[tuple[Path, Path]] = queue.Queue(maxsize=1000)
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


def _run_worker(q: queue.Queue | None = None, *, once: bool = False) -> None:
    """The background worker's loop: take a saved text, index it. ``q`` and ``once`` exist for tests."""
    source = q if q is not None else _pending
    while True:
        root, path = source.get()
        index_text_file(root, path)
        if once:
            return


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, daemon=True, name="anthill-doc-index")
            _worker.start()


def index_soon(root: Path, path: Path) -> None:
    """Queue a just-kept text for the background worker. Returns at once and never raises."""
    if not ENABLED or not support()[0]:
        return
    try:
        _pending.put_nowait((root, path))
        _ensure_worker()
    except queue.Full:
        log.debug("doc index queue is full, %s is indexed by the next startup pass", path)
    except Exception as e:
        log.debug("could not queue %s for the doc index: %s", path, e)
