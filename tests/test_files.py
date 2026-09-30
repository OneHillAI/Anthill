"""File creation: html/md/txt/pdf + office formats + charts (deps gated, no network)."""

import pytest

from anthill.multimodal import files, images


def test_sidecar_collects_selfcheck_office_dependencies():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("Anthill-sidecar.spec").read_text())
    collected = {
        item.value
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_pkg"
        and isinstance(node.iter, ast.Tuple)
        for item in node.iter.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }

    assert {"docx", "pptx", "openpyxl", "lxml"} <= collected


def test_unsupported_format_raises(tmp_path):
    with pytest.raises(ValueError):
        files.create("x", "rtf", tmp_path / "x.rtf")


def test_txt_and_md_are_raw(tmp_path):
    p = files.create("hello\nworld", "txt", tmp_path / "a.txt")
    assert p.read_text() == "hello\nworld"
    p2 = files.create("# Title", "md", tmp_path / "a.md")
    assert p2.read_text() == "# Title"


def test_html_structure_and_escaping(tmp_path):
    out = files.create(
        "# Heading\n\nSome **bold** and <script>evil</script>.\n\n- one\n- two",
        "html",
        tmp_path / "a.html",
        title="Doc",
    )
    txt = out.read_text()
    assert "<!doctype html>" in txt.lower()
    assert "<h1>Heading</h1>" in txt
    assert "<strong>bold</strong>" in txt
    assert "<li>one</li>" in txt and "<li>two</li>" in txt
    assert "&lt;script&gt;" in txt and "<script>evil" not in txt  # escaped, not injected


def test_pdf_magic(tmp_path):
    out = files.create("# Report\n\nBody text.", "pdf", tmp_path / "a.pdf", title="Report")
    assert out.read_bytes()[:4] == b"%PDF"


def test_rows_from_markdown_table():
    rows = files._rows_from_content("| Name | Qty |\n|---|---|\n| Apples | 3 |\n| Pears | 5 |")
    assert rows[0] == ["Name", "Qty"]  # header
    assert ["Apples", "3"] in rows and ["Pears", "5"] in rows
    assert all(r != ["---", "---"] for r in rows)  # separator row dropped


def test_rows_from_csv():
    rows = files._rows_from_content("a,b,c\n1,2,3")
    assert rows == [["a", "b", "c"], ["1", "2", "3"]]


# ── office formats need the optional [docs] libs - skip cleanly if absent ──────


def test_docx_is_zip(tmp_path):
    pytest.importorskip("docx")
    out = files.create("# Title\n\nA paragraph.\n- bullet", "docx", tmp_path / "a.docx")
    assert out.read_bytes()[:2] == b"PK"


def test_pptx_is_zip(tmp_path):
    pytest.importorskip("pptx")
    out = files.create(
        "# Slide one\n- point a\n- point b\n# Slide two\n- next", "pptx", tmp_path / "a.pptx"
    )
    assert out.read_bytes()[:2] == b"PK"


def test_xlsx_is_zip(tmp_path):
    pytest.importorskip("openpyxl")
    out = files.create("| A | B |\n|---|---|\n| 1 | 2 |", "xlsx", tmp_path / "a.xlsx")
    assert out.read_bytes()[:2] == b"PK"


# ── packaged-app smoke test (guards the DMG bundle) ────────────────────────────


def test_selfcheck_office_validates_every_format(capsys):
    """`Anthill --selfcheck-office` (run against the frozen binary by scripts/build-app.sh) must
    create + validate one of each format. It is the guard for the packaging bug where python-docx /
    python-pptx import fine but their bundled default template is missing, so create() fails only in
    the app. Here we run it in-process with the libs present: it must report every format OK and
    exit 0."""
    pytest.importorskip("docx")
    pytest.importorskip("pptx")
    pytest.importorskip("openpyxl")
    from anthill.desktop import _selfcheck_office

    assert _selfcheck_office() == 0
    out = capsys.readouterr().out
    for fmt in ("docx", "pptx", "xlsx", "pdf"):
        assert f"{fmt} OK" in out
    assert "pdf-ingest OK" in out
    assert "ALL OK" in out


# ── charts via Pillow (a core dependency) ──────────────────────────────────────


def test_bar_chart_png(tmp_path):
    out = images.bar_chart(["Q1", "Q2", "Q3"], [10, 25, 17], tmp_path / "c.png", title="Sales")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_bar_chart_jpg(tmp_path):
    out = images.bar_chart(["a", "b"], [1, 2], tmp_path / "c.jpg")
    assert out.read_bytes()[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_bar_chart_handles_empty(tmp_path):
    out = images.bar_chart([], [], tmp_path / "c.png")
    assert out.is_file() and out.stat().st_size > 0


# ── content preview for the chat pane ──────────────────────────────────────────


def test_preview_unsupported_type(tmp_path):
    p = files.create("plain", "txt", tmp_path / "a.txt")
    assert "No inline preview" in files.preview_html(p)


def test_preview_xlsx_renders_table(tmp_path):
    pytest.importorskip("openpyxl")
    p = files.create("| Name | Qty |\n|---|---|\n| Apples | 3 |", "xlsx", tmp_path / "a.xlsx")
    html = files.preview_html(p)
    assert "<table>" in html and "Apples" in html and "Qty" in html


def test_preview_docx_renders_text(tmp_path):
    pytest.importorskip("docx")
    p = files.create("# Title\n\nHello body.", "docx", tmp_path / "a.docx")
    html = files.preview_html(p)
    assert "Title" in html and "Hello body." in html


def test_preview_pptx_lists_slides(tmp_path):
    pytest.importorskip("pptx")
    p = files.create("# One\n- a\n# Two\n- b", "pptx", tmp_path / "a.pptx")
    assert "Slide 1" in files.preview_html(p)


def test_preview_escapes_html(tmp_path):
    pytest.importorskip("docx")
    p = files.create("Danger <script>alert(1)</script> here", "docx", tmp_path / "a.docx")
    html = files.preview_html(p)
    assert "&lt;script&gt;" in html and "<script>alert" not in html
