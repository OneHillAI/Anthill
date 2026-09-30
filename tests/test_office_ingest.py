"""Ingestion of Office documents and HTML (#683 phase 4): MarkItDown's non-PDF converters are
now wired up in `anthill.multimodal.reader._read_office`, mirroring `_read_pdf`'s pattern but
without its safety-limit/image-extraction machinery (out of scope for this phase).

Fixtures are small, REAL files built with python-docx/python-pptx/openpyxl (not garbage bytes
with a faked extension) so these tests exercise the actual MarkItDown conversion path.
"""

from pathlib import Path

import pytest

from anthill.multimodal.reader import FileContent
from anthill.wiki.ingest import ingest
from anthill.wiki.workspace import Workspace


class RecordingBackend:
    model = "test-model"

    def __init__(self, reply: str | None = None):
        self.calls = []
        self._reply = reply

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self._reply is not None:
            return self._reply
        # Default: echo something derived from the source so tests can assert on it.
        return "# Ingested\n\nSummarised content."


def _docx_fixture(path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_heading("Quarterly Report", level=1)
    document.add_paragraph("Revenue grew eight percent in the office fixture.")
    document.save(path)


def _pptx_fixture(path: Path) -> None:
    import pptx

    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Roadmap"
    presentation.save(path)


def _xlsx_fixture(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet["A1"] = "Team"
    sheet["B1"] = "Spend"
    sheet["A2"] = "Platform"
    sheet["B2"] = 4200
    workbook.save(path)


def _html_fixture(path: Path) -> None:
    path.write_text(
        "<html><head><title>Runbook</title></head>"
        "<body><h1>Deploy runbook</h1><p>Deploys happen on Fridays.</p></body></html>",
        encoding="utf-8",
    )


# --- anthill.multimodal.reader.read_file --------------------------------------------------


def test_read_docx_produces_structured_markdown(tmp_path):
    from anthill.multimodal.reader import read_file

    source = tmp_path / "report.docx"
    _docx_fixture(source)

    fc = read_file(source)

    assert isinstance(fc, FileContent)
    assert "Quarterly Report" in fc.text
    assert "Revenue grew eight percent" in fc.text
    assert fc.mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert fc.images_b64 == []
    assert fc.pdf_preflight is None  # no PDF safety machinery applies to office files


def test_read_pptx_produces_structured_markdown(tmp_path):
    from anthill.multimodal.reader import read_file

    source = tmp_path / "deck.pptx"
    _pptx_fixture(source)

    fc = read_file(source)

    assert "Roadmap" in fc.text
    assert fc.mime_type == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert fc.pdf_preflight is None


def test_read_xlsx_produces_structured_markdown(tmp_path):
    from anthill.multimodal.reader import read_file

    source = tmp_path / "budget.xlsx"
    _xlsx_fixture(source)

    fc = read_file(source)

    assert "Budget" in fc.text  # sheet name heading
    assert "Platform" in fc.text
    assert "4200" in fc.text
    assert fc.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert fc.pdf_preflight is None


def test_read_html_produces_structured_markdown(tmp_path):
    from anthill.multimodal.reader import read_file

    source = tmp_path / "runbook.html"
    _html_fixture(source)

    fc = read_file(source)

    assert "Deploy runbook" in fc.text
    assert "Deploys happen on Fridays" in fc.text
    assert fc.mime_type == "text/html"
    assert fc.pdf_preflight is None


def test_read_htm_extension_is_also_dispatched_to_html_converter(tmp_path):
    """`.htm` is a distinct suffix from `.html` in the dispatch table; make sure it isn't missed."""
    from anthill.multimodal.reader import read_file

    source = tmp_path / "runbook.htm"
    _html_fixture(source)

    fc = read_file(source)

    assert "Deploy runbook" in fc.text
    assert fc.mime_type == "text/html"


def test_read_empty_html_reports_no_extractable_text(tmp_path):
    from anthill.multimodal.reader import read_file

    source = tmp_path / "blank.html"
    source.write_text("<html><body></body></html>", encoding="utf-8")

    fc = read_file(source)

    assert "no extractable text" in fc.text


# --- anthill.wiki.ingest.ingest (the same pipeline wiki_upload uses) ------------------------


@pytest.mark.parametrize(
    "fixture_name, filename",
    [
        ("_docx_fixture", "report.docx"),
        ("_pptx_fixture", "deck.pptx"),
        ("_xlsx_fixture", "budget.xlsx"),
        ("_html_fixture", "runbook.html"),
    ],
)
def test_ingest_office_formats_writes_a_summarised_wiki_page(tmp_path, fixture_name, filename):
    """The full `ingest()` pipeline (read -> summarise -> write page) works end to end for
    every new format, exactly as it already does for PDFs and plain text."""
    fixture_fn = globals()[fixture_name]
    source = tmp_path / filename
    fixture_fn(source)

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend("# Ingested\n\nSummarised content from the fixture.")

    page = ingest(workspace, source, backend)

    assert page is not None
    text = page.read_text()
    assert "Summarised content from the fixture" in text
    assert backend.calls  # the summariser actually ran (not raw text filed as-is)


def test_ingest_office_document_never_triggers_pdf_confirmation(tmp_path):
    """`_preflight_pdf_work` only acts when `mime_type == 'application/pdf'`; confirm a large-ish
    non-PDF document is never routed through the PDF confirmation/limit gate, even without
    `allow_large_pdf`."""
    source = tmp_path / "report.docx"
    _docx_fixture(source)

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    # allow_large_pdf defaults to False - if the PDF preflight mistakenly fired for this
    # non-PDF FileContent, this would raise PdfConfirmationRequired.
    page = ingest(workspace, source, backend, allow_large_pdf=False)

    assert page is not None
