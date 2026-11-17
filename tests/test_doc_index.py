"""The passage index of the kept document texts (issue #226, step 1). A kept text is split into passages that
can always be read back from the file by their offsets, indexed once per content, ranked by BM25 inside SQLite
and kept in line with the texts that exist. Nothing here uses a model."""

import queue
import re
import sqlite3

import pytest

from anthill.wiki import doc_index
from anthill.wiki import ingest as ingest_mod
from anthill.wiki.doc_index import PASSAGE_CHARS, DocIndex, split_document
from anthill.wiki.workspace import Workspace
from tests.test_upload_status import _Backend


def _ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    return ws


def _keep(ws: Workspace, name: str, text: str):
    ws.text.mkdir(exist_ok=True)
    path = ws.text / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _nonspace(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ── splitting ─────────────────────────────────────────────────────────────────

SAMPLES = {
    "short": "A short note about the bakery.",
    "paragraphs": "\n\n".join(
        f"Paragraph {i} says something about topic {i}." * 3 for i in range(60)
    ),
    "long paragraph": ("The owner bakes bread at four in the morning. " * 80).strip(),
    "no spaces": "x" * 2500,
    "unicode": "Café crème brûlée. " * 10 + "\n\n" + "ขนมปังอบใหม่ทุกเช้า " * 30,
    "windows": "First paragraph.\r\n\r\nSecond paragraph.\r\n\r\n" + "More text here. " * 100,
    "headings": "# Title\n\n" + "Intro. " * 100 + "\n\n## Section B\n\n" + "Details. " * 100,
}


@pytest.mark.parametrize("name", list(SAMPLES))
def test_every_passage_reads_back_from_the_text_by_its_offsets(name):
    text = SAMPLES[name]
    passages = split_document(text)
    assert passages, name
    previous_stop = 0
    for i, p in enumerate(passages):
        assert p.n == i
        assert text[p.start : p.stop] == p.text
        assert p.text == p.text.strip() and p.text
        assert len(p.text) <= PASSAGE_CHARS
        assert p.start >= previous_stop  # in order, not overlapping
        previous_stop = p.stop
    assert _nonspace("".join(p.text for p in passages)) == _nonspace(text)  # nothing left out


def test_nothing_in_an_empty_text():
    assert split_document("") == [] and split_document(" \n\n \t ") == []


def test_short_paragraphs_are_packed_into_passages_of_about_the_target_size():
    text = "\n\n".join(f"Paragraph number {i:03d} with some words." for i in range(100))
    passages = split_document(text)
    assert 2 <= len(passages) < 20
    assert all(len(p.text) <= PASSAGE_CHARS for p in passages)
    assert (
        sum(len(p.text) > PASSAGE_CHARS - 100 for p in passages[:-1]) == len(passages) - 1
    )  # full


def test_a_paragraph_longer_than_a_passage_is_cut_at_spaces_not_in_words():
    words = split_document(SAMPLES["long paragraph"])
    assert len(words) >= 3
    for p in words:
        assert p.text.startswith("The") or p.text.startswith("owner") or p.text[0].isalpha()
        assert re.fullmatch(r"[A-Za-z .]+", p.text) and not p.text.endswith(" ")
    glued = split_document(SAMPLES["no spaces"])
    assert [len(p.text) for p in glued] == [PASSAGE_CHARS, PASSAGE_CHARS, PASSAGE_CHARS, 100]


def test_pdf_pages_are_counted_and_a_passage_never_spans_two_pages():
    text = "Page one text.\n\n---\n\nPage two text.\n\n---\n\nPage three, a little longer."
    passages = split_document(text, pdf=True)
    assert [p.page for p in passages] == [1, 2, 3]
    assert [p.text for p in passages] == [
        "Page one text.",
        "Page two text.",
        "Page three, a little longer.",
    ]
    assert all("---" not in p.text for p in passages)
    assert all(p.page is None for p in split_document(text))  # not a PDF: no page numbers


def test_a_passage_knows_the_heading_above_it():
    text = "# Title\n\n" + "Intro. " * 150 + "\n\n## Section B\n\n" + "Details. " * 150
    passages = split_document(text)
    assert passages[0].heading == "Title"
    assert passages[-1].heading == "Section B"
    assert split_document("no heading here at all")[0].heading == ""


# ── the index ─────────────────────────────────────────────────────────────────


def _index(tmp_path) -> DocIndex:
    return DocIndex(tmp_path / "cache", tmp_path / "text")


def _add(idx: DocIndex, doc: str, text: str, *, pdf: bool = False) -> int:
    """Keep the text as ingest does, then index it."""
    idx.text_dir.mkdir(exist_ok=True)
    (idx.text_dir / f"{doc}.txt").write_bytes(text.encode("utf-8", "surrogateescape"))
    return idx.index(doc, text, pdf=pdf)


def test_a_search_finds_the_passage_and_reports_where_it_is(tmp_path):
    text = (
        "The bakery opens at five.\n\n"
        + ("Filler about weather. " * 60)
        + "\n\nThe vault code is ZEBRA-4417."
    )
    idx = _index(tmp_path)
    assert _add(idx, "report-1a2b3c4d5e6f.html", text) > 0

    hit = idx.search("what is the vault code")[0]
    assert "ZEBRA-4417" in hit.text and hit.doc == "report-1a2b3c4d5e6f.html"
    assert text[hit.start : hit.stop] == hit.text  # the offsets lead back into the kept text


def test_the_better_match_ranks_first_across_documents(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "a.pdf", "Quarterly revenue grew in March.", pdf=True)
    _add(
        idx, "b.html", "The vault code for the maintenance cabinet is 4417 and the cabinet is blue."
    )
    top = idx.search("vault code maintenance cabinet")
    assert top[0].doc == "b.html" and {h.doc for h in top} <= {"a.pdf", "b.html"}


def test_a_pdf_passage_reports_its_page(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "r.pdf", "Intro page.\n\n---\n\nThe refund window is forty five days.", pdf=True)
    (hit,) = idx.search("refund window")
    assert hit.page == 2


def test_an_unchanged_text_is_not_indexed_again_and_a_changed_one_replaces_its_passages(tmp_path):
    idx = _index(tmp_path)
    assert _add(idx, "a.html", "The old fact is zebra.") == 1
    assert _add(idx, "a.html", "The old fact is zebra.") == 0  # same content: nothing to do
    assert _add(idx, "a.html", "The new fact is otter.") == 1
    assert idx.search("zebra") == [] and len(idx.search("otter")) == 1
    assert len(idx.docs()) == 1


def test_dropping_a_text_drops_its_passages(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "a.html", "alpha beta")
    _add(idx, "b.html", "alpha gamma")
    idx.drop("a.html")
    assert [h.doc for h in idx.search("alpha")] == ["b.html"]
    assert list(idx.docs()) == ["b.html"]


@pytest.mark.parametrize(
    "query", ["", "   ", "!!! ???", '"', "OR AND NOT", "NEAR(a b)", "col:val*", "a" * 5000]
)
def test_a_query_with_syntax_characters_never_raises(tmp_path, query):
    idx = _index(tmp_path)
    _add(idx, "a.html", "alpha beta gamma")
    assert isinstance(idx.search(query), list)


def test_accents_and_case_do_not_hide_a_match(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "a.html", "Le café crème est excellent.")
    assert len(idx.search("CAFE creme")) == 1


def test_limit_caps_the_hits(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "a.html", "\n\n".join(f"alpha paragraph {i} " * 40 for i in range(20)))
    assert len(idx.search("alpha", limit=3)) == 3 and idx.search("alpha", limit=0) == []


def test_a_damaged_index_is_rebuilt(tmp_path):
    idx = _index(tmp_path)
    idx.path.parent.mkdir(parents=True)
    idx.path.write_bytes(b"this is not a sqlite database at all" * 100)
    assert _add(idx, "a.html", "alpha beta") == 1
    assert len(idx.search("alpha")) == 1


def test_without_full_text_search_nothing_breaks(tmp_path, monkeypatch):
    def _no_fts5(self):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(DocIndex, "_connect", _no_fts5)
    idx = _index(tmp_path)
    assert _add(idx, "a.html", "alpha") == 0 and idx.search("alpha") == [] and idx.docs() == {}
    idx.drop("a.html")


def test_an_index_that_cannot_be_opened_is_skipped(tmp_path):
    (tmp_path / "cache").write_text("a file where the folder should be")
    idx = _index(tmp_path)
    assert _add(idx, "a.html", "alpha") == 0 and idx.search("alpha") == []


# ── keeping it in line with the texts ─────────────────────────────────────────


def test_sync_indexes_new_texts_skips_unchanged_ones_and_drops_gone_ones(tmp_path):
    ws = _ws(tmp_path)
    a = _keep(ws, "a-1a2b3c4d5e6f.html", "alpha zebra")
    _keep(ws, "r-1a2b3c4d5e6f.pdf", "Page one.\n\n---\n\nrefund window")
    assert doc_index.sync_workspace(ws.root) == 3  # one passage, and two (a page each)
    assert doc_index.sync_workspace(ws.root) == 0  # nothing changed
    idx = DocIndex(ws.root / ".cache")
    assert idx.search("refund")[0].page == 2  # a ".pdf" text is split as a PDF

    a.write_text("alpha otter changed", encoding="utf-8")
    assert doc_index.sync_workspace(ws.root) == 1
    assert idx.search("otter") and not idx.search("zebra")

    a.unlink()
    doc_index.sync_workspace(ws.root)
    assert list(idx.docs()) == ["r-1a2b3c4d5e6f.pdf"]


def test_a_workspace_with_no_kept_text_creates_no_index(tmp_path):
    ws = _ws(tmp_path)
    assert doc_index.sync_workspace(ws.root) == 0
    assert not (ws.root / ".cache" / doc_index.DB_NAME).exists()


def test_a_text_that_cannot_be_read_is_skipped(tmp_path):
    ws = _ws(tmp_path)
    assert doc_index.index_text_file(ws.root, ws.text / "missing.txt") == 0


def test_the_startup_pass_indexes_texts_kept_before_the_index_existed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(doc_index, "ENABLED", True)
    ws = Workspace(tmp_path / "wikis" / "user-1", scope="personal")
    ws.init()
    _keep(ws, "old-1a2b3c4d5e6f.html", "kept long before the index existed")
    assert doc_index.sync_all() >= 1
    assert DocIndex(ws.root / ".cache").search("existed")


# ── in the background, after ingest ───────────────────────────────────────────


@pytest.mark.usefixtures("summarise_every_note")
def test_ingest_queues_the_index_and_does_not_build_it_itself(tmp_path, monkeypatch):
    q: queue.Queue = queue.Queue()
    monkeypatch.setattr(doc_index, "ENABLED", True)
    monkeypatch.setattr(doc_index, "_pending", q)
    monkeypatch.setattr(
        doc_index, "_ensure_worker", lambda: None
    )  # no thread: the test is the worker
    ws = _ws(tmp_path)
    source = tmp_path / "report.html"
    source.write_text("<html><body><h1>Q1</h1><p>The vault code is ZEBRA-4417.</p></body></html>")

    ingest_mod.ingest(ws, source, _Backend())

    assert not (ws.root / ".cache" / doc_index.DB_NAME).exists()  # ingest did not build it
    assert q.qsize() == 1  # it queued it
    doc_index._run_worker(q, once=True)
    (hit,) = DocIndex(ws.root / ".cache").search("vault code")
    assert "ZEBRA-4417" in hit.text and hit.doc.startswith("report-") and hit.doc.endswith(".html")


@pytest.mark.usefixtures("summarise_every_note")
def test_nothing_is_queued_for_a_note_or_when_the_index_is_off(tmp_path, monkeypatch):
    q: queue.Queue = queue.Queue()
    monkeypatch.setattr(doc_index, "_pending", q)
    monkeypatch.setattr(doc_index, "_ensure_worker", lambda: None)
    ws = _ws(tmp_path)
    note = tmp_path / "note.md"
    note.write_text("# A note\n\nThe owner bakes at 4am.\n")
    page = tmp_path / "report.html"
    page.write_text("<html><body><p>Some real text in a document.</p></body></html>")

    monkeypatch.setattr(doc_index, "ENABLED", True)
    ingest_mod.ingest(ws, note, _Backend())
    assert q.qsize() == 0  # a note has no kept text

    monkeypatch.setattr(doc_index, "ENABLED", False)
    ingest_mod.ingest(ws, page, _Backend())
    assert q.qsize() == 0  # switched off


def test_a_full_queue_never_raises(tmp_path, monkeypatch):
    q: queue.Queue = queue.Queue(maxsize=1)
    q.put(("x", "y"))
    monkeypatch.setattr(doc_index, "ENABLED", True)
    monkeypatch.setattr(doc_index, "_pending", q)
    monkeypatch.setattr(doc_index, "_ensure_worker", lambda: None)
    doc_index.index_soon(tmp_path, tmp_path / "x.txt")  # does not raise


# ── the index points into the text file; it does not copy it ──────────────────


def test_the_index_holds_no_copy_of_the_text(tmp_path):
    phrase = "The Quick Brown Fox, jumps (twice) over THE lazy dog!"
    idx = _index(tmp_path)
    _add(idx, "a.html", phrase + "\n\n" + "Filler words about weather. " * 40)
    assert idx.search("quick fox")  # it finds it...
    assert phrase.encode() not in idx.path.read_bytes()  # ...without having kept it


def test_the_index_is_smaller_than_the_text_it_covers(tmp_path):
    import random

    random.seed(3)
    words = [f"w{i}" for i in range(4000)]
    text = "\n\n".join(" ".join(random.choice(words) for _ in range(90)) + "." for _ in range(700))
    idx = _index(tmp_path)
    _add(idx, "big.pdf", text, pdf=True)
    assert idx.path.stat().st_size < len(text.encode())


def test_a_hit_is_dropped_when_its_text_file_changed_or_is_gone(tmp_path):
    idx = _index(tmp_path)
    _add(idx, "a.html", "The vault code is ZEBRA-4417 in the cabinet.")
    path = idx.text_dir / "a.html.txt"
    assert len(idx.search("vault code")) == 1

    original = path.read_bytes()
    path.write_bytes(original.replace(b"ZEBRA", b"OTTER"))  # same length, different bytes
    assert idx.search("vault code") == []
    path.unlink()
    assert idx.search("vault code") == []
    path.write_bytes(original)
    assert len(idx.search("vault code")) == 1  # the file is as indexed again


@pytest.mark.parametrize(
    "text",
    [
        "Café crème brûlée est délicieux. " * 60
        + "\n\n"
        + "Le mot rare est ÉLÉPHANT ici."
        + "\n\n"
        + "Fin. " * 80,
        "ขนมปังอบใหม่ทุกเช้า " * 60 + "\n\n" + "ข้อความสุดท้าย zebra ที่นี่" + "\n\n" + "จบ " * 200,
        "日本語のテキスト。" * 100 + "\n\n" + "最後の段落 zebra です。",
    ],
)
def test_non_ascii_text_reads_back_exactly_by_byte_offsets(tmp_path, text):
    idx = _index(tmp_path)
    assert _add(idx, "a.html", text) >= 1
    for query in ("zebra", "ELEPHANT"):
        for hit in idx.search(query):
            assert text[hit.start : hit.stop] == hit.text


def test_invalid_bytes_in_a_text_file_do_not_break_indexing_or_reading(tmp_path):
    ws = _ws(tmp_path)
    path = ws.text / "odd-1a2b3c4d5e6f.html.txt"
    ws.text.mkdir()
    path.write_bytes(
        b"Before the bad bytes. " * 30
        + b"\xff\xfe broken \x80 "
        + b"After they come the zebra. " * 30
    )
    assert doc_index.index_text_file(ws.root, path) >= 1
    hits = DocIndex(ws.root / ".cache").search("zebra")
    assert hits and "zebra" in hits[0].text
