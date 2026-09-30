"""Model-free tests for the lifecycle module (provenance + stale detection)."""

import pytest

from anthill.lifecycle.version import (
    PROVENANCE_RE,
    read_provenance,
    stale_pages,
    tag_page,
)


def test_tag_page_adds_provenance():
    out = tag_page("# Title\n\nSummary.\n\n## Related\n[[x]]", "qwen3:8b", on="2026-06-02")
    prov = read_provenance(out)
    assert prov == {"model": "qwen3:8b", "date": "2026-06-02"}
    # original content preserved
    assert "# Title" in out and "[[x]]" in out


def test_tag_page_replaces_existing():
    once = tag_page(
        "# T\n\nbody",
        "qwen2.5:3b",
        on="2026-01-01",
        source="report-a1b2c3.pdf",
    )
    twice = tag_page(once, "qwen3:8b", on="2026-06-02")
    # only one provenance comment remains, the newer one
    assert len(PROVENANCE_RE.findall(twice)) == 1
    assert read_provenance(twice)["model"] == "qwen3:8b"
    assert read_provenance(twice)["source"] == "report-a1b2c3.pdf"


def test_tag_page_records_raw_source_identifier():
    out = tag_page(
        "# T\n\nbody",
        "qwen3:8b",
        on="2026-06-02",
        source="quarterly report-a1b2c3.pdf",
    )

    assert read_provenance(out) == {
        "model": "qwen3:8b",
        "date": "2026-06-02",
        "source": "quarterly report-a1b2c3.pdf",
    }


def test_read_provenance_none_when_untagged():
    assert read_provenance("# Plain page\n\nno tag here") is None


def test_provenance_is_trailing_and_invisible_to_summary():
    from anthill.common.text import first_h1

    out = tag_page("# Database\n\nWe chose Postgres.\n\n## Related\n[[db]]", "m:1")
    # H1 and the summary line are untouched at the top
    assert first_h1(out) == "Database"
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "# Database"
    assert lines[1] == "We chose Postgres."
    # the comment is the last non-empty line
    assert lines[-1].startswith("<!-- anthill:")


def test_stale_pages_compares_configured_pipeline_model_for_vision_pages(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = tag_page(
        "# Scan\n\nVision summary.",
        "qwen3.5:9b",
        pipeline_model="qwen2.5:3b",
    )
    (wiki / "scan.md").write_text(page)

    assert stale_pages(wiki, "qwen2.5:3b") == []
    assert stale_pages(wiki, "qwen3:8b")[0][1] == "qwen2.5:3b"
    assert read_provenance(page)["pipeline_model"] == "qwen2.5:3b"


def test_lifecycle_status_uses_pipeline_model_for_row_marker(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from anthill import cli
    from anthill.wiki.workspace import Workspace

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    workspace.write_page(
        "Scan",
        tag_page(
            "# Scan\n\nVision summary.",
            "qwen3.5:9b",
            pipeline_model="qwen2.5:3b",
        ),
    )
    monkeypatch.setattr(cli, "_ws", lambda ctx: workspace)
    ctx = SimpleNamespace(obj={"config": SimpleNamespace(model="qwen2.5:3b")})

    cli.lifecycle_status(ctx)

    output = capsys.readouterr().out
    assert "stale: 0" in output
    assert "← stale" not in output


def test_stale_pages_flags_mismatched_and_untagged(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "a.md").write_text(tag_page("# A\n\nx", "qwen3:8b"))
    (wiki / "b.md").write_text(tag_page("# B\n\ny", "qwen2.5:3b"))
    (wiki / "c.md").write_text("# C\n\nuntagged")
    stale = stale_pages(wiki, "qwen3:8b")
    stale_slugs = {p.stem for p, _ in stale}
    assert stale_slugs == {"b", "c"}  # a is current, b is old model, c untagged
    # recorded model is reported
    recorded = {p.stem: m for p, m in stale}
    assert recorded["b"] == "qwen2.5:3b"
    assert recorded["c"] is None


@pytest.mark.model_invariant
def test_eval_result_winner_logic():
    from anthill.lifecycle.evaluate import EvalResult

    r = EvalResult("a", "b", 10, score_a=0.80, score_b=0.70, wins_a=7, wins_b=3, ties=0)
    assert r.winner == "a"
    tie = EvalResult("a", "b", 10, score_a=0.805, score_b=0.800, wins_a=5, wins_b=5, ties=0)
    assert tie.winner == "tie"


def test_renovate_skips_when_no_source(tmp_path):
    """A page with no matching raw/ source is reported skipped, not crashed."""
    from anthill.lifecycle.renovate import renovate
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "w")
    ws.init()
    # write a page tagged with an old model, but put nothing in raw/
    ws.write_page("Orphan Topic", tag_page("# Orphan Topic\n\nbody", "old:1"))

    class _DummyBackend:
        model = "new:1"

        def chat(self, messages, **kw):
            return "# Orphan Topic\n\nregenerated"

    report = renovate(ws, _DummyBackend(), "new:1", only_stale=True)
    assert "orphan-topic" in report.skipped
    assert report.regenerated == []


def test_renovate_uses_recorded_content_addressed_source(tmp_path):
    from anthill.lifecycle.renovate import renovate
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "w")
    ws.init()
    raw_source = ws.raw / "quarterly-report-a1b2c3.txt"
    raw_source.write_text("Revenue increased by eight percent.")
    ws.write_page(
        "Different Topic",
        tag_page(
            "# Different Topic\n\nOld summary.",
            "old:1",
            source=raw_source.name,
        ),
    )

    class _RecordingBackend:
        model = "new:1"

        def __init__(self):
            self.messages = []

        def chat(self, messages, **kwargs):
            self.messages.append(messages)
            return "# Different Topic\n\nUpdated summary."

    backend = _RecordingBackend()
    report = renovate(ws, backend, "new:1", only_stale=True)

    assert report.regenerated == ["different-topic"]
    assert "Revenue increased by eight percent." in backend.messages[0][-1].content
    provenance = read_provenance((ws.wiki / "different-topic.md").read_text())
    assert provenance["source"] == raw_source.name


def test_renovate_reuses_vision_ingestion(tmp_path, monkeypatch):
    from anthill.lifecycle.renovate import renovate
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "w")
    ws.init()
    raw_source = ws.raw / "scan-a1b2c3.pdf"
    raw_source.write_bytes(b"scan")
    ws.write_page(
        "Scanned Invoice",
        tag_page("# Scanned Invoice\n\nOld summary.", "old:1", source=raw_source.name),
    )
    monkeypatch.setattr(
        "anthill.wiki.ingest.read_file",
        lambda source, **kwargs: FileContent(
            text="[PDF: scan.pdf - no extractable text. Images require vision extraction.]",
            images_b64=["page-image"],
            mime_type="application/pdf",
            source_name=source.name,
            pdf_preflight=PdfPreflight(
                page_count=1,
                image_count=1,
                decoded_image_bytes=1,
                vision_required=True,
            ),
        ),
    )

    from anthill.inference.ollama import OllamaBackend

    backend = OllamaBackend("http://localhost:11434", "new:1")
    messages_seen = []

    def chat(messages, **kwargs):
        messages_seen.append(messages)
        return "# Scanned Invoice\n\nUpdated summary."

    monkeypatch.setattr(backend, "chat", chat)
    monkeypatch.setattr(
        "anthill.routing.router.TaskRouter._installed_models",
        lambda self: {"granite3.2-vision:2b"},
    )
    renovate(ws, backend, "new:1")

    assert any(call[-1].images == ["page-image"] for call in messages_seen)


def test_renovate_skips_pdf_limit_and_continues(tmp_path, monkeypatch):
    import importlib

    renovate_module = importlib.import_module("anthill.lifecycle.renovate")
    from anthill.wiki.ingest import PdfConfirmationRequired
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "w")
    ws.init()
    for name in ("blocked.pdf", "ready.txt"):
        (ws.raw / name).write_text("source")
    ws.write_page("Blocked", tag_page("# Blocked\n\nold", "old:1", source="blocked.pdf"))
    ws.write_page("Ready", tag_page("# Ready\n\nold", "old:1", source="ready.txt"))

    def source_to_page(_ws, source, _backend, **_kwargs):
        if source.name == "blocked.pdf":
            raise PdfConfirmationRequired("confirmation required")
        return "# Ready\n\nnew", "new:1"

    monkeypatch.setattr(renovate_module, "source_to_page", source_to_page)
    report = renovate_module.renovate(ws, object(), "new:1")

    assert any(item.startswith("blocked:") for item in report.skipped)
    assert report.regenerated == ["ready"]


def test_renovate_reuses_bounded_text_reduction(tmp_path):
    from anthill.lifecycle.renovate import renovate
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "w")
    ws.init()
    raw_source = ws.raw / "long-report-a1b2c3.txt"
    raw_source.write_text(("Measured operational result. " * 100 + "\n\n") * 20)
    ws.write_page(
        "Long Report",
        tag_page("# Long Report\n\nOld summary.", "old:1", source=raw_source.name),
    )

    class _RecordingBackend:
        model = "new:1"

        def __init__(self):
            self.messages = []

        def chat(self, messages, **kwargs):
            self.messages.append(messages)
            return "S" * 6_000

    backend = _RecordingBackend()
    renovate(ws, backend, "new:1")

    assert len(backend.messages) > 2
    assert all(len(call[-1].content) < 15_000 for call in backend.messages)
