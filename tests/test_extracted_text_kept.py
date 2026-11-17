"""The text Anthill extracts from a document is kept beside the original, so a person can see what the AI
was given and the text can be searched later. Model-free: the model is a fake backend."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from anthill.multimodal.reader import FileContent
from anthill.wiki import ingest as ingest_mod
from anthill.wiki.ingest import keep_extracted_text
from anthill.wiki.workspace import Workspace
from tests.test_upload_status import _Backend, _login, _setup, _text_pdf, _upload

REPORT_TEXT = "Revenue grew in March and the bakery opened a second shop."


def _ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    return ws


# ── the helper ────────────────────────────────────────────────────────────────


def test_the_extracted_text_is_kept_under_the_originals_name(tmp_path):
    ws = _ws(tmp_path)
    raw = ws.raw / "report-1a2b3c4d5e6f.pdf"
    kept = keep_extracted_text(ws, raw, FileContent(text=REPORT_TEXT))
    assert kept == ws.text / "report-1a2b3c4d5e6f.pdf.txt"
    assert kept.read_text(encoding="utf-8") == REPORT_TEXT
    assert not list(ws.text.glob("*.tmp"))


@pytest.mark.parametrize("name", ["note.md", "note.txt", "NOTE.TXT"])
def test_nothing_is_kept_for_a_file_that_already_is_text(tmp_path, name):
    ws = _ws(tmp_path)
    assert keep_extracted_text(ws, ws.raw / name, FileContent(text="# A note\n\nbody")) is None
    assert not ws.text.exists()


@pytest.mark.parametrize(
    "stand_in",
    [
        "[Image: photo.png]",
        "[DOCX: a.docx - no extractable text.]",
        "[PDF: scan.pdf - no extractable text. Images require vision extraction.]",
        "",
        "   \n  ",
    ],
)
def test_nothing_is_kept_when_the_reader_found_no_text(tmp_path, stand_in):
    ws = _ws(tmp_path)
    assert (
        keep_extracted_text(ws, ws.raw / "x-1a2b3c4d5e6f.pdf", FileContent(text=stand_in)) is None
    )
    assert not ws.text.exists()


def test_a_document_that_really_says_something_in_brackets_is_kept(tmp_path):
    ws = _ws(tmp_path)
    text = "[Image: the chart on page 3]\n\nRevenue is up.\n"  # more than the one stand-in line
    assert keep_extracted_text(ws, ws.raw / "x-1a2b3c4d5e6f.pdf", FileContent(text=text))


def test_a_very_long_text_is_cut_with_a_line_that_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_mod, "EXTRACTED_TEXT_MAX_CHARS", 50)
    ws = _ws(tmp_path)
    kept = keep_extracted_text(ws, ws.raw / "big-1a2b3c4d5e6f.pdf", FileContent(text="x" * 500))
    body = kept.read_text(encoding="utf-8")
    assert body.startswith("x" * 50) and "x" * 51 not in body
    assert "Anthill stopped keeping the text here" in body and "50" in body


def test_keeping_it_again_replaces_it_and_leaves_nothing_behind(tmp_path):
    ws = _ws(tmp_path)
    raw = ws.raw / "report-1a2b3c4d5e6f.pdf"
    keep_extracted_text(ws, raw, FileContent(text="first reading"))
    kept = keep_extracted_text(ws, raw, FileContent(text="a better reading"))
    assert kept.read_text(encoding="utf-8") == "a better reading"
    assert [p.name for p in ws.text.iterdir()] == ["report-1a2b3c4d5e6f.pdf.txt"]


def test_a_failure_to_write_never_fails_the_upload(tmp_path, monkeypatch):
    ws = _ws(tmp_path)

    def _boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    assert keep_extracted_text(ws, ws.raw / "x-1a2b3c4d5e6f.pdf", FileContent(text="text")) is None


# ── through ingest ────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("summarise_every_note")
def test_ingest_keeps_the_text_it_gave_the_model(tmp_path):
    ws = _ws(tmp_path)
    source = tmp_path / "report.html"
    source.write_text(f"<html><body><h1>Q1</h1><p>{REPORT_TEXT}</p></body></html>")
    ingest_mod.ingest(ws, source, _Backend())

    (raw,) = [p for p in ws.raw.iterdir() if p.suffix == ".html"]
    kept = (ws.text / f"{raw.name}.txt").read_text(encoding="utf-8")
    assert REPORT_TEXT in kept and "<p>" not in kept  # the extracted text, not the markup
    assert (ws.text / f"{raw.name}.txt").stat().st_size > 0


@pytest.mark.usefixtures("summarise_every_note")
def test_ingest_keeps_nothing_beside_a_note(tmp_path):
    ws = _ws(tmp_path)
    source = tmp_path / "note.md"
    source.write_text("# Bakery interview\n\nThe owner bakes at 4am.\n")
    ingest_mod.ingest(ws, source, _Backend())
    assert not ws.text.exists()


# ── the wiki page ─────────────────────────────────────────────────────────────


def _html(client, url):
    return BeautifulSoup(client.get(url).text, "html.parser")


@pytest.mark.usefixtures("summarise_every_note")
def test_a_person_can_open_the_text_from_the_files_tab_and_the_page(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    location = _upload(client, "report.pdf", _text_pdf(REPORT_TEXT)).headers["location"]
    slug = location.split("slug=")[1].split("&")[0]

    files = _html(client, "/wiki?tab=files")
    link = files.find("a", string="text read from it")
    assert link is not None and link["href"].startswith("/wiki/text/report-")
    assert "the text Anthill read out of the document" in files.get_text()

    opened = client.get(link["href"])
    assert opened.status_code == 200
    assert opened.headers["content-type"].startswith("text/plain")
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert REPORT_TEXT in opened.text

    page = _html(client, f"/wiki/page/{slug}").select_one("p.text-muted")
    text = " ".join(page.get_text().split())
    assert (
        "the original is kept under Files, and you can open the text Anthill read from it." in text
    )
    assert page.find("a", string="open the text Anthill read from it")["href"] == link["href"]


@pytest.mark.usefixtures("summarise_every_note")
def test_a_note_has_no_text_link(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    location = _upload(client, "bakery.md", b"# Bakery\n\nThe owner bakes at 4am.\n").headers[
        "location"
    ]
    slug = location.split("slug=")[1].split("&")[0]
    assert _html(client, "/wiki?tab=files").find("a", string="text read from it") is None
    assert "open the text" not in client.get(f"/wiki/page/{slug}").text


@pytest.mark.usefixtures("summarise_every_note")
def test_only_a_kept_original_has_text_to_open(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    _upload(client, "report.pdf", _text_pdf(REPORT_TEXT))
    name = _html(client, "/wiki?tab=files").find("a", string="text read from it")["href"]
    name = name.split("/wiki/text/")[1].split("?")[0]

    assert client.get(f"/wiki/text/{name}").status_code == 200
    assert client.get("/wiki/text/nothing-1a2b3c4d5e6f.pdf").status_code == 404
    assert client.get("/wiki/text/..%2F..%2Fsecret.txt").status_code == 404
    assert (
        client.get(f"/wiki/text/{name}.txt").status_code == 404
    )  # the text file's own name is not a key
    # another person's wiki has no such file
    _login(client, ids["mate"])
    assert client.get(f"/wiki/text/{name}").status_code == 404
