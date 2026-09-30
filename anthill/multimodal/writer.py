from __future__ import annotations

import re
from pathlib import Path


def markdown_to_pdf(markdown: str, output_path: Path, title: str = "") -> Path:
    """Convert a markdown string to a PDF file using reportlab.

    Handles: # headings, ## sub-headings, **bold**, *italic*, bullet lists,
    numbered lists, code blocks (```), and plain paragraphs.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            ListFlowable,
            ListItem,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as e:
        raise ImportError("Install reportlab: pip install reportlab>=4.0") from e

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )
    styles = getSampleStyleSheet()

    # Custom styles
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20, spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=15, spaceAfter=8)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=12, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=11, leading=16, spaceAfter=8)
    code_s = ParagraphStyle(
        "Code", parent=styles["Code"], fontSize=9, backColor=colors.HexColor("#f1f3f5"), leading=13
    )

    story = []
    if title:
        story.append(Paragraph(title, h1))
        story.append(Spacer(1, 0.3 * cm))

    in_code = False
    code_buf: list[str] = []
    bullet_buf: list[str] = []

    def flush_bullets():
        if bullet_buf:
            items = [ListItem(Paragraph(b, body)) for b in bullet_buf]
            story.append(ListFlowable(items, bulletType="bullet"))
            story.append(Spacer(1, 0.2 * cm))
            bullet_buf.clear()

    def flush_code():
        if code_buf:
            story.append(Preformatted("\n".join(code_buf), code_s))
            story.append(Spacer(1, 0.2 * cm))
            code_buf.clear()

    for line in markdown.splitlines():
        if line.startswith("```"):
            if in_code:
                flush_code()
            else:
                flush_bullets()
            in_code = not in_code
            continue

        if in_code:
            code_buf.append(line)
            continue

        # Headings
        if line.startswith("### "):
            flush_bullets()
            story.append(Paragraph(_inline(line[4:]), h3))
        elif line.startswith("## "):
            flush_bullets()
            story.append(Paragraph(_inline(line[3:]), h2))
        elif line.startswith("# "):
            flush_bullets()
            story.append(Paragraph(_inline(line[2:]), h1))
        elif line.startswith(("- ", "* ", "• ")):
            bullet_buf.append(_inline(line[2:]))
        elif re.match(r"^\d+\. ", line):
            bullet_buf.append(_inline(re.sub(r"^\d+\. ", "", line)))
        elif line.strip() == "":
            flush_bullets()
            story.append(Spacer(1, 0.15 * cm))
        else:
            flush_bullets()
            story.append(Paragraph(_inline(line), body))

    flush_bullets()
    flush_code()

    doc.build(story)
    return output_path


def _inline(text: str) -> str:
    """Convert inline markdown (**bold**, *italic*, `code`) to reportlab XML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", text)
    return text
