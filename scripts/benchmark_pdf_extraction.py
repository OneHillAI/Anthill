#!/usr/bin/env python3
"""Compare Anthill's former pypdf extraction with local MarkItDown conversion."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
from markitdown import MarkItDown, StreamInfo
from markitdown.converters import PdfConverter
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "benchmarks" / "pdf"


def pypdf_text(path: Path) -> str:
    return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def markitdown_text(path: Path) -> str:
    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    converter.register_converter(PdfConverter())
    with path.open("rb") as stream:
        result = converter.convert_stream(
            stream,
            stream_info=StreamInfo(
                mimetype="application/pdf",
                extension=".pdf",
                filename=path.name,
                local_path=str(path),
            ),
        )
    return result.markdown


def measure(extractor: Callable[[Path], str], path: Path) -> tuple[str, float, float]:
    tracemalloc.start()
    started = time.perf_counter()
    text = extractor(path)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return text, elapsed_ms, peak / 1024


def _contains_answer_term(answer: str, term: str) -> bool:
    normalized_answer = re.sub(r"\W", "", answer.casefold())
    normalized_term = re.sub(r"\W", "", term.casefold())
    return normalized_term in normalized_answer


def ollama_answer(text: str, question: str, *, base_url: str, model: str) -> str:
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer using only the supplied document. Be brief and factual.",
                },
                {"role": "user", "content": f"DOCUMENT:\n{text}\n\nQUESTION: {question}"},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0, "num_predict": 80},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def anthill_vision_page(path: Path, *, base_url: str, model: str) -> str:
    from anthill.inference.ollama import OllamaBackend
    from anthill.wiki.ingest import ingest
    from anthill.wiki.workspace import Workspace

    backend = OllamaBackend(base_url, model, timeout=300)
    with tempfile.TemporaryDirectory() as directory:
        workspace = Workspace(Path(directory) / "wiki")
        workspace.init()
        page = ingest(workspace, path, backend)
        if page is None:
            raise RuntimeError("Anthill vision benchmark did not produce a wiki page.")
        return page.read_text()


def run(
    corpus: Path,
    *,
    ollama_url: str = "",
    model: str = "",
    anthill_vision_e2e: bool = False,
) -> list[dict]:
    manifest = json.loads((corpus / "manifest.json").read_text())
    results = []
    for filename, expected in manifest.items():
        path = corpus / filename
        for converter, extractor in (("pypdf", pypdf_text), ("markitdown", markitdown_text)):
            text, elapsed_ms, peak_kib = measure(extractor, path)
            terms = expected["terms"]
            found = sum(term.casefold() in text.casefold() for term in terms)
            row = {
                "fixture": filename,
                "converter": converter,
                "term_recall": round(found / len(terms), 2),
                "markdown_table": "\n| " in f"\n{text}" if expected.get("table") else None,
                "characters": len(text),
                "latency_ms": round(elapsed_ms, 1),
                "peak_kib": round(peak_kib, 1),
            }
            if model:
                answer = ollama_answer(text, expected["question"], base_url=ollama_url, model=model)
                answer_terms = expected["answer_terms"]
                answer_found = sum(_contains_answer_term(answer, term) for term in answer_terms)
                row["answer_recall"] = round(answer_found / len(answer_terms), 2)
                row["answer"] = answer
            results.append(row)
        if anthill_vision_e2e and expected.get("vision_required"):
            page, elapsed_ms, peak_kib = measure(
                lambda source: anthill_vision_page(
                    source,
                    base_url=ollama_url,
                    model=model,
                ),
                path,
            )
            answer_terms = expected["answer_terms"]
            answer_found = sum(_contains_answer_term(page, term) for term in answer_terms)
            answer_recall = round(answer_found / len(answer_terms), 2)
            results.append(
                {
                    "fixture": filename,
                    "converter": "anthill-local-vision",
                    "term_recall": None,
                    "markdown_table": None,
                    "characters": len(page),
                    "latency_ms": round(elapsed_ms, 1),
                    "peak_kib": round(peak_kib, 1),
                    "answer_recall": answer_recall,
                    "answer": page,
                }
            )
            if answer_recall < 1:
                missing = [term for term in answer_terms if not _contains_answer_term(page, term)]
                raise RuntimeError(
                    "Anthill local-vision E2E missed raster-only facts: " + ", ".join(missing)
                )
    return results


def markdown(results: list[dict]) -> str:
    with_answers = any("answer_recall" in row for row in results)
    answer_heading = " Answer recall |" if with_answers else ""
    answer_separator = "---:|" if with_answers else ""
    lines = [
        f"| Fixture | Converter | Recall | Markdown table | Characters | Latency ms | Peak KiB |{answer_heading}",
        f"|---|---|---:|:---:|---:|---:|---:|{answer_separator}",
    ]
    for row in results:
        table = "n/a" if row["markdown_table"] is None else str(row["markdown_table"]).lower()
        answer = f" {row['answer_recall']:.2f} |" if with_answers else ""
        recall = "n/a" if row["term_recall"] is None else f"{row['term_recall']:.2f}"
        lines.append(
            f"| {row['fixture']} | {row['converter']} | {recall} | {table} | "
            f"{row['characters']} | {row['latency_ms']:.1f} | {row['peak_kib']:.1f} |{answer}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--anthill-vision-e2e", action="store_true")
    args = parser.parse_args()
    if args.anthill_vision_e2e:
        host = (urlparse(args.ollama_url).hostname or "").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            parser.error("--anthill-vision-e2e requires a loopback Ollama URL")
        if not args.ollama_model:
            parser.error("--anthill-vision-e2e requires --ollama-model")
    results = run(
        args.corpus,
        ollama_url=args.ollama_url,
        model=args.ollama_model,
        anthill_vision_e2e=args.anthill_vision_e2e,
    )
    print(json.dumps(results, indent=2) if args.json else markdown(results))


if __name__ == "__main__":
    main()
