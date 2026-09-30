import json

import pytest

from scripts import benchmark_pdf_extraction as benchmark


def _mixed_corpus(tmp_path):
    manifest = {
        "mixed.pdf": {
            "terms": ["Deployment topology"],
            "question": "Describe the component flow.",
            "answer_terms": ["Queue", "Worker", "Archive"],
            "vision_required": True,
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "mixed.pdf").write_bytes(b"fixture")
    return tmp_path


def test_benchmark_runs_anthill_local_vision_e2e(tmp_path, monkeypatch):
    corpus = _mixed_corpus(tmp_path)
    monkeypatch.setattr(benchmark, "pypdf_text", lambda path: "Deployment topology")
    monkeypatch.setattr(benchmark, "markitdown_text", lambda path: "Deployment topology")
    monkeypatch.setattr(benchmark, "ollama_answer", lambda *args, **kwargs: "No visual facts.")
    monkeypatch.setattr(
        benchmark,
        "anthill_vision_page",
        lambda *args, **kwargs: "Queue sends work through Worker to Archive.",
    )

    results = benchmark.run(
        corpus,
        ollama_url="http://127.0.0.1:11434",
        model="qwen2.5:3b",
        anthill_vision_e2e=True,
    )

    vision_row = results[-1]
    assert vision_row["converter"] == "anthill-local-vision"
    assert vision_row["answer_recall"] == 1.0
    assert "| mixed.pdf | anthill-local-vision | n/a |" in benchmark.markdown(results)


def test_benchmark_fails_when_local_vision_misses_raster_fact(tmp_path, monkeypatch):
    corpus = _mixed_corpus(tmp_path)
    monkeypatch.setattr(benchmark, "pypdf_text", lambda path: "Deployment topology")
    monkeypatch.setattr(benchmark, "markitdown_text", lambda path: "Deployment topology")
    monkeypatch.setattr(benchmark, "ollama_answer", lambda *args, **kwargs: "No visual facts.")
    monkeypatch.setattr(
        benchmark,
        "anthill_vision_page",
        lambda *args, **kwargs: "Queue sends work to Worker.",
    )

    with pytest.raises(RuntimeError, match="Archive"):
        benchmark.run(
            corpus,
            ollama_url="http://127.0.0.1:11434",
            model="qwen2.5:3b",
            anthill_vision_e2e=True,
        )


def test_canonical_benchmark_runs_scanned_and_mixed_vision_fixtures(monkeypatch):
    seen = []
    monkeypatch.setattr(benchmark, "pypdf_text", lambda path: "")
    monkeypatch.setattr(benchmark, "markitdown_text", lambda path: "")

    def vision_page(path, **kwargs):
        seen.append(path.name)
        if path.name == "scanned.pdf":
            return "Purchase order PO-8841 has an approved total of 4,250 EUR."
        return "Queue sends work through Worker to Archive."

    monkeypatch.setattr(benchmark, "anthill_vision_page", vision_page)

    results = benchmark.run(
        benchmark.DEFAULT_CORPUS,
        ollama_url="http://127.0.0.1:11434",
        anthill_vision_e2e=True,
    )

    vision_fixtures = {
        row["fixture"] for row in results if row["converter"] == "anthill-local-vision"
    }
    assert seen == ["scanned.pdf", "mixed-diagram.pdf"]
    assert vision_fixtures == {"scanned.pdf", "mixed-diagram.pdf"}
