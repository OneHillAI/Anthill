#!/usr/bin/env python3
"""Generate the small, synthetic PDF corpus used by benchmark_pdf_extraction.py."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "pdf"


def _canvas(name: str) -> Canvas:
    ROOT.mkdir(parents=True, exist_ok=True)
    return Canvas(str(ROOT / name), pagesize=letter, invariant=True)


def _text_heavy() -> None:
    canvas = _canvas("text-heavy.pdf")
    for page in range(3):
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(54, 750, f"Operations handbook - chapter {page + 1}")
        canvas.setFont("Helvetica", 10)
        for line in range(30):
            canvas.drawString(
                54,
                720 - line * 20,
                f"Policy {page + 1}.{line + 1}: retain invoices for seven years and review quarterly.",
            )
        canvas.showPage()
    canvas.save()


def _scanned() -> None:
    image_path = ROOT / "scanned-source.png"
    image = Image.new("RGB", (900, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 80), "SCANNED PURCHASE ORDER PO-8841", fill="black")
    draw.text((60, 180), "Approved total: 4,250 EUR", fill="black")
    image.save(image_path)
    canvas = _canvas("scanned.pdf")
    canvas.drawImage(str(image_path), 54, 450, width=504, height=224)
    canvas.save()
    image_path.unlink()


def _table_form() -> None:
    canvas = _canvas("table-form.pdf")
    rows = (
        ("Region", "Revenue", "Growth"),
        ("Europe", "120000", "8 percent"),
        ("Americas", "95000", "5 percent"),
        ("Asia Pacific", "143000", "11 percent"),
    )
    for y, row in zip((730, 705, 680, 655), rows, strict=True):
        for x, value in zip((54, 260, 440), row, strict=True):
            canvas.drawString(x, y, value)
    canvas.save()


def _multi_column() -> None:
    canvas = _canvas("multi-column.pdf")
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(54, 750, "Resilience review")
    canvas.setFont("Helvetica", 10)
    left = (
        "Primary systems stayed online",
        "Backups completed at midnight",
        "Recovery target: 15 min",
    )
    right = (
        "Latency stayed below 80 ms",
        "Cache hit rate reached 72 percent",
        "Next drill: September",
    )
    for line, text in enumerate(left):
        canvas.drawString(54, 710 - line * 24, text)
    for line, text in enumerate(right):
        canvas.drawString(320, 710 - line * 24, text)
    canvas.save()


def _mixed_diagram() -> None:
    image_path = ROOT / "mixed-diagram-source.png"
    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    for x, label in ((40, "Queue"), (340, "Worker"), (640, "Archive")):
        draw.rectangle((x, 105, x + 220, 195), outline="black", width=4)
        draw.text((x + 80, 140), label, fill="black")
    draw.line((260, 150, 340, 150), fill="black", width=5)
    draw.line((320, 135, 340, 150, 320, 165), fill="black", width=5)
    draw.line((560, 150, 640, 150), fill="black", width=5)
    draw.line((620, 135, 640, 150, 620, 165), fill="black", width=5)
    image.save(image_path)
    canvas = _canvas("mixed-diagram.pdf")
    canvas.drawString(54, 750, "Deployment topology")
    canvas.drawString(54, 725, "Component relationships are shown only in the raster diagram.")
    canvas.drawImage(str(image_path), 54, 470, width=504, height=168)
    canvas.save()
    image_path.unlink()


def _long() -> None:
    canvas = _canvas("long.pdf")
    for page in range(20):
        canvas.drawString(54, 750, f"Annual archive page {page + 1}")
        for line in range(35):
            canvas.drawString(
                54, 725 - line * 18, f"Record {page + 1}-{line + 1}: verified retention entry"
            )
        canvas.showPage()
    canvas.save()


def main() -> None:
    _text_heavy()
    _scanned()
    _table_form()
    _multi_column()
    _mixed_diagram()
    _long()
    manifest = {
        "text-heavy.pdf": {
            "terms": ["Operations handbook", "retain invoices", "review quarterly"],
            "question": "How long are invoices retained, and how often are they reviewed?",
            "answer_terms": ["seven years", "quarterly"],
        },
        "scanned.pdf": {
            "terms": ["SCANNED PURCHASE ORDER", "PO-8841", "4,250 EUR"],
            "question": "What is the purchase order number and approved total?",
            "answer_terms": ["PO-8841", "4,250"],
        },
        "table-form.pdf": {
            "terms": ["Region", "Europe", "Asia Pacific"],
            "table": True,
            "question": "Which region has the highest revenue, and what is its growth?",
            "answer_terms": ["Asia Pacific", "143000", "11 percent"],
        },
        "multi-column.pdf": {
            "terms": ["Primary systems", "Latency", "Next drill"],
            "question": "What cache hit rate was reached, and when is the next drill?",
            "answer_terms": ["72 percent", "September"],
        },
        "mixed-diagram.pdf": {
            "terms": ["Deployment topology", "relationships", "raster diagram"],
            "question": "Describe the traffic flow between the three components.",
            "answer_terms": ["Queue", "Worker", "Archive"],
            "vision_required": True,
        },
        "long.pdf": {
            "terms": ["Annual archive page 1", "Record 10-20", "Record 20-35"],
            "question": "What is the final record on archive page 20?",
            "answer_terms": ["Record 20-35"],
        },
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
