"""Persistent per-workspace page-vector index (spec: docs/specs/wiki-page-vector-index.md).

Ranking used to embed every candidate wiki page on every question: one HTTP call to the embedding model
per page, so the cost of a question grew with the wiki and landed in the time to the first word. The
index stores one vector per page next to the semantic cache, keyed by the page path, a hash of exactly
the text that is embedded and the embedding model name, so a page is embedded once and again only when
its text changes. Model-free: a fake `emb.embed` over a hashed bag of words counts every call."""

import re
import sqlite3
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from anthill.cache import page_vectors
from anthill.cache.page_vectors import PageVectorIndex
from anthill.common.text import strip_frontmatter
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace

DIM = 32
TOPICS = {
    "billing": "invoice invoices payment stripe billing",
    "hiring": "interview candidate hiring recruiter offer",
    "roadmap": "roadmap milestone release quarter plan",
    "security": "security audit encryption password breach",
}
N_PAGES = 16


def _vec(text: str) -> np.ndarray:
    """A deterministic L2-normalised float32 vector: hashed bag of words."""
    v = np.zeros(DIM, dtype=np.float32)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(tok.encode()) % DIM] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class _Fake:
    """Stands in for `emb.embed`: same vectors as `_vec`, but records every text it is asked to embed."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail_on: str | None = None

    def __call__(self, text):
        self.calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("embedder down")
        return _vec(text)


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    monkeypatch.setattr(ask_mod.emb, "embed", f)
    return f


def _page_text(i: int) -> str:
    topic = list(TOPICS)[i % len(TOPICS)]
    return f"# {topic.title()} note {i}\n\n{TOPICS[topic]} {TOPICS[topic]} unique{i} detail{i}\n"


def _wiki(tmp_path: Path, name: str = "w", n: int = N_PAGES) -> Workspace:
    ws = Workspace(tmp_path / name)
    ws.init()
    for i in range(n):
        (ws.wiki / f"page-{i:02d}.md").write_text(_page_text(i))
    return ws


def _old_rank(paths, q_vec, k):
    """The implementation this change replaced: embed every page on every call. Uses the same pure
    vectors, so any difference in the result is a difference introduced by the index."""
    scored = []
    for p in paths:
        v = _vec(strip_frontmatter(p.read_text())[:2000])
        s = ask_mod.emb.cosine(q_vec, v)
        if s >= ask_mod.MIN_GROUNDING_SIM:
            scored.append((s, p, v))
    scored.sort(key=lambda x: x[0], reverse=True)
    return ask_mod._mmr_select(scored, k)


def _db(ws: Workspace) -> Path:
    return ws.root / ".cache" / page_vectors.DB_NAME


def _rows(ws: Workspace) -> dict[str, tuple[str, str]]:
    con = sqlite3.connect(_db(ws))
    try:
        rows = con.execute(f"SELECT path, model, content_hash FROM {page_vectors.TABLE}").fetchall()
    finally:
        con.close()
    return {path: (model, h) for path, model, h in rows}


def _ask(ws: Workspace, question: str, k: int = 3) -> list[Path]:
    """What a question does: `_relevant_pages` over the whole wiki, question vector computed up front
    (so the fake's call list holds page texts only)."""
    return ask_mod._relevant_pages(ws, question, k, _vec(question))


QUESTIONS = [
    "stripe invoice payment",
    "interview candidate offer",
    "roadmap milestone for the quarter",
    "security audit password",
    "invoice interview roadmap",
    "zebra crossing banana",  # nothing related: every page falls under the grounding floor
]


# ── the index itself ───────────────────────────────────────────────────────────


def test_index_embeds_each_text_once_and_returns_identical_vectors(tmp_path, fake):
    index = PageVectorIndex(tmp_path / ".cache")
    items = [(f"wiki/p{i}.md", _page_text(i)) for i in range(5)]

    cold = index.vectors(items, fake, "m1")
    assert len(fake.calls) == 5
    for (_k, text), v in zip(items, cold, strict=True):
        assert v.dtype == np.float32
        assert np.array_equal(v, _vec(text))

    warm = index.vectors(items, fake, "m1")
    assert len(fake.calls) == 5  # nothing embedded again
    for a, b in zip(cold, warm, strict=True):
        assert np.array_equal(a, b)


def test_index_is_per_page_text_hash_and_model(tmp_path, fake):
    index = PageVectorIndex(tmp_path / ".cache")
    items = [("wiki/a.md", "alpha one"), ("wiki/b.md", "beta two")]
    index.vectors(items, fake, "m1")
    fake.calls.clear()

    index.vectors([("wiki/a.md", "alpha one"), ("wiki/b.md", "beta CHANGED")], fake, "m1")
    assert fake.calls == ["beta CHANGED"]  # only the changed text

    fake.calls.clear()
    index.vectors(items, fake, "m2")  # a different embedding model never reuses the old vectors
    assert sorted(fake.calls) == ["alpha one", "beta two"]


def test_tampered_row_is_a_miss_not_a_wrong_vector(tmp_path, fake):
    index = PageVectorIndex(tmp_path / ".cache")
    items = [("wiki/a.md", "alpha one")]
    index.vectors(items, fake, "m1")
    fake.calls.clear()

    con = sqlite3.connect(tmp_path / ".cache" / page_vectors.DB_NAME)
    con.execute(f"UPDATE {page_vectors.TABLE} SET vec = ?", (b"\x00\x01\x02",))  # wrong length
    con.commit()
    con.close()

    (v,) = index.vectors(items, fake, "m1")
    assert fake.calls == ["alpha one"]  # re-embedded
    assert np.array_equal(v, _vec("alpha one"))


# ── ranking through the index ──────────────────────────────────────────────────


def test_first_question_embeds_every_page_second_embeds_none(tmp_path, fake):
    ws = _wiki(tmp_path)
    first = _ask(ws, QUESTIONS[0])
    assert len(fake.calls) == N_PAGES
    assert len(set(fake.calls)) == N_PAGES  # each page exactly once

    fake.calls.clear()
    second = _ask(ws, QUESTIONS[1])  # a different question over the same pages
    assert fake.calls == []
    assert first and second and first != second


@pytest.mark.parametrize("k", [1, 3, 5])
def test_ranking_equals_the_uncached_implementation_cold_and_warm(tmp_path, fake, k):
    ws = _wiki(tmp_path)
    expected_any = False
    for phase in ("cold", "warm"):
        for q in QUESTIONS:
            want = _old_rank(ws.pages(), _vec(q), k)
            assert _ask(ws, q, k) == want, f"{phase}: {q!r}"
            expected_any = expected_any or bool(want)
    assert expected_any  # the questions really select pages, so the comparison means something
    assert _old_rank(ws.pages(), _vec(QUESTIONS[-1]), k) == []  # and the floor still drops the rest


def test_editing_one_page_reembeds_exactly_that_page(tmp_path, fake):
    ws = _wiki(tmp_path)
    _ask(ws, QUESTIONS[0])
    fake.calls.clear()

    target = ws.wiki / "page-05.md"
    target.write_text("# Billing rewritten\n\nstripe invoice refund policy changed\n")
    got = _ask(ws, "stripe refund policy")
    assert fake.calls == ["# Billing rewritten\n\nstripe invoice refund policy changed\n"]
    assert got == _old_rank(ws.pages(), _vec("stripe refund policy"), 3)
    assert target in got  # the new text is what ranked, not the stale vector

    fake.calls.clear()
    _ask(ws, "stripe refund policy")
    assert fake.calls == []


def test_frontmatter_and_text_past_2000_chars_do_not_reembed(tmp_path, fake):
    ws = Workspace(tmp_path / "w")
    ws.init()
    body = "# Long page\n\n" + "stripe invoice " * 200  # well over 2000 characters
    ws.write_page("long-page", body)
    _ask(ws, "stripe invoice")
    assert len(fake.calls) == 1 and len(fake.calls[0]) == 2000
    fake.calls.clear()

    ws.write_page("long-page", body)  # new timestamp in the frontmatter, same prose
    ws.write_page("long-page", body + " more words beyond the embedded window")
    _ask(ws, "stripe invoice")
    assert (
        fake.calls == []
    )  # the embedded text (frontmatter stripped, first 2000 characters) is the same


def test_deleting_a_page_drops_its_row(tmp_path, fake):
    ws = _wiki(tmp_path)
    _ask(ws, QUESTIONS[0])
    assert len(_rows(ws)) == N_PAGES

    (ws.wiki / "page-03.md").unlink()
    got = _ask(ws, QUESTIONS[0])
    assert "wiki/page-03.md" not in _rows(ws)
    assert len(_rows(ws)) == N_PAGES - 1
    assert got == _old_rank(ws.pages(), _vec(QUESTIONS[0]), 3)


def test_rows_for_a_changed_model_are_replaced_not_accumulated(tmp_path, fake, monkeypatch):
    ws = _wiki(tmp_path)
    _ask(ws, QUESTIONS[0])
    assert {m for m, _h in _rows(ws).values()} == {ask_mod.emb.MODEL_NAME}

    monkeypatch.setattr(ask_mod.emb, "MODEL_NAME", "some-other-model")
    fake.calls.clear()
    _ask(ws, QUESTIONS[0])
    assert len(fake.calls) == N_PAGES  # every page re-embedded for the new model
    assert {m for m, _h in _rows(ws).values()} == {"some-other-model"}
    assert len(_rows(ws)) == N_PAGES

    fake.calls.clear()
    _ask(ws, QUESTIONS[1])
    assert fake.calls == []


# ── safe by construction ───────────────────────────────────────────────────────


def test_corrupt_index_file_falls_back_and_heals(tmp_path, fake):
    ws = _wiki(tmp_path)
    (ws.root / ".cache").mkdir(exist_ok=True)
    _db(ws).write_bytes(b"this is not a sqlite database at all " * 100)

    for q in QUESTIONS:
        assert _ask(ws, q) == _old_rank(ws.pages(), _vec(q), 3)  # correct, no exception
    # the unreadable file was replaced, so the pages were embedded once and then reused
    assert len(fake.calls) == N_PAGES
    assert len(_rows(ws)) == N_PAGES


def test_unreadable_index_path_falls_back_to_embedding_directly(tmp_path, fake):
    ws = _wiki(tmp_path)
    (ws.root / ".cache").mkdir(exist_ok=True)
    _db(ws).mkdir()  # a directory where the database file should be: cannot be opened

    q = QUESTIONS[0]
    assert _ask(ws, q) == _old_rank(ws.pages(), _vec(q), 3)
    assert len(fake.calls) == N_PAGES
    assert _ask(ws, q) == _old_rank(ws.pages(), _vec(q), 3)
    assert len(fake.calls) == 2 * N_PAGES  # today's behaviour: embed directly every time


def test_unwritable_cache_dir_falls_back(tmp_path, fake):
    ws = _wiki(tmp_path)
    (ws.root / ".cache").write_text("a file where the cache directory should be")

    q = QUESTIONS[1]
    assert _ask(ws, q) == _old_rank(ws.pages(), _vec(q), 3)
    assert len(fake.calls) == N_PAGES


def test_locked_database_falls_back_and_recovers(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(page_vectors, "BUSY_TIMEOUT_S", 0.05)
    ws = _wiki(tmp_path)
    _ask(ws, QUESTIONS[0])  # creates the database
    (ws.wiki / "page-07.md").write_text("# Hiring changed\n\ninterview candidate offer revised\n")
    fake.calls.clear()

    locker = sqlite3.connect(_db(ws), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        q = "interview candidate offer"
        assert _ask(ws, q) == _old_rank(ws.pages(), _vec(q), 3)  # no exception, right answer
        assert len(fake.calls) == N_PAGES  # could not read the index, so it embedded directly
    finally:
        locker.execute("ROLLBACK")
        locker.close()

    fake.calls.clear()
    _ask(ws, "interview candidate offer")  # lock gone: only the changed page is embedded and stored
    assert fake.calls == ["# Hiring changed\n\ninterview candidate offer revised\n"]
    fake.calls.clear()
    _ask(ws, "roadmap milestone")
    assert fake.calls == []


def test_embedder_failure_propagates_as_before_and_keeps_progress(tmp_path, fake):
    ws = _wiki(tmp_path)
    fake.fail_on = "unique5 "  # page-05 fails, the five before it succeed
    with pytest.raises(RuntimeError, match="embedder down"):
        ask_mod._rank_by_embedding(ws.pages(), "stripe", 3, _vec("stripe"), roots=[ws.root])
    assert len(_rows(ws)) == 5  # what was embedded is kept

    fake.fail_on = None
    fake.calls.clear()
    got = _ask(ws, "stripe invoice payment")
    assert len(fake.calls) == N_PAGES - 5  # only the pages still missing
    assert got == _old_rank(ws.pages(), _vec("stripe invoice payment"), 3)


def test_embedder_failure_in_a_question_degrades_to_keyword_search(tmp_path, fake):
    ws = _wiki(tmp_path)
    fake.fail_on = "unique"  # every page fails
    got = ask_mod._relevant_pages(ws, "stripe invoice payment", 3, _vec("stripe invoice payment"))
    assert got  # the existing keyword fallback still answers
    assert all(p in ws.pages() for p in got)


def test_no_embedder_means_no_index_is_touched(tmp_path, monkeypatch):
    ws = _wiki(tmp_path)

    def _boom(text):
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(ask_mod.emb, "embed", _boom)
    got = ask_mod._relevant_pages(ws, "stripe invoice payment", 3, None)  # keyword fallback
    assert got
    assert not _db(ws).exists()


def test_concurrent_questions_are_correct_and_leave_a_complete_index(tmp_path, fake):
    ws = _wiki(tmp_path)
    expected = {q: _old_rank(ws.pages(), _vec(q), 3) for q in QUESTIONS}
    barrier = threading.Barrier(8)

    def _worker(i):
        barrier.wait()
        q = QUESTIONS[i % len(QUESTIONS)]
        return q, _ask(ws, q)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_worker, range(8)))
    for q, got in results:
        assert got == expected[q]
    assert len(_rows(ws)) == N_PAGES

    fake.calls.clear()
    _ask(ws, QUESTIONS[0])
    assert fake.calls == []


# ── scoping: per workspace, only under a known root ────────────────────────────


def test_page_outside_every_root_is_embedded_directly_and_not_stored(tmp_path, fake):
    ws = _wiki(tmp_path)
    stray = tmp_path / "stray.md"
    stray.write_text("# Stray\n\nstripe invoice payment\n")
    for _ in range(2):
        assert ask_mod._rank_by_embedding([stray], "stripe", 3, _vec("stripe invoice payment")) == [
            stray
        ]
        assert ask_mod._rank_by_embedding(
            [stray], "stripe", 3, _vec("stripe invoice payment"), roots=[ws.root]
        ) == [stray]
    assert len(fake.calls) == 4  # never cached
    assert not _db(ws).exists()


def test_two_workspaces_keep_separate_indexes_even_for_the_same_slug(tmp_path, fake):
    a, b = _wiki(tmp_path, "a", n=6), _wiki(tmp_path, "b", n=6)
    (b.wiki / "page-00.md").write_text("# Other\n\nentirely different words about gardens\n")
    ask_mod._merge_relevant([a, b], "stripe invoice payment", 2, _vec("stripe invoice payment"))
    assert _rows(a).keys() == _rows(b).keys()
    assert (
        _rows(a)["wiki/page-00.md"][1] != _rows(b)["wiki/page-00.md"][1]
    )  # different text, own row


def test_merge_relevant_equals_uncached_and_never_prunes_unlisted_pages(tmp_path, fake):
    a, b = _wiki(tmp_path, "a"), _wiki(tmp_path, "b")
    (b.wiki / "page-00.md").write_text("# Billing b\n\nstripe invoice payment for team b\n")
    k = 3
    for q in QUESTIONS[:3]:
        qv = _vec(q)
        cands = _old_rank(a.pages(), qv, k) + _old_rank(b.pages(), qv, k)
        uniq = list(dict.fromkeys(cands))
        want = _old_rank(uniq, qv, k) if len(uniq) > k else uniq
        assert ask_mod._merge_relevant([a, b], q, k, qv) == want
    # the blended re-rank only sees a handful of pages, which must not look like "the rest were deleted"
    assert len(_rows(a)) == N_PAGES and len(_rows(b)) == N_PAGES

    fake.calls.clear()
    ask_mod._merge_relevant([a, b], QUESTIONS[3], k, _vec(QUESTIONS[3]))
    assert fake.calls == []


# ── the real call paths ────────────────────────────────────────────────────────


class _Cache:  # no lancedb in these tests: only retrieval is under test
    def __init__(self, **k):
        pass

    def lookup(self, q):
        return None

    def store(self, *a, **k):
        pass


class _Backend:
    def chat(self, messages, **k):
        return "ok"

    def chat_stream(self, messages, **k):
        yield "ok"


def test_ask_stream_and_ask_reuse_page_vectors_across_questions(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(ask_mod.emb, "safe_embed", _vec)
    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    ws = _wiki(tmp_path)

    used: list[list[str]] = []
    list(ask_mod.ask_stream(ws, QUESTIONS[0], _Backend(), on_context=used.append))
    assert len(fake.calls) == N_PAGES  # the first question builds the index
    list(ask_mod.ask_stream(ws, QUESTIONS[1], _Backend(), on_context=used.append))
    _answer, slugs, _hit = ask_mod.ask(ws, QUESTIONS[2], _Backend())
    ask_mod.ask(ws, QUESTIONS[3], _Backend())
    assert len(fake.calls) == N_PAGES  # ... and no later question, on either path, embeds a page
    assert used[0] and used[1] and used[0] != used[1] and slugs


def test_index_file_coexists_with_the_semantic_cache(tmp_path, fake, monkeypatch):
    from anthill.cache.cache import SemanticCache

    monkeypatch.setattr(ask_mod.emb, "safe_embed", lambda p: np.ones(1024, dtype=np.float32))
    ws = _wiki(tmp_path)
    _ask(ws, QUESTIONS[0])
    assert _db(ws).exists()

    cache = SemanticCache(db_path=ws.root / ".cache", threshold=0.5)
    cache.store("which payment provider do we use", "Stripe")
    hit = cache.lookup("which payment provider do we use")
    assert hit is not None and hit.answer == "Stripe"
    fake.calls.clear()
    _ask(ws, QUESTIONS[1])
    assert fake.calls == []  # and the semantic cache did not disturb the page vectors
