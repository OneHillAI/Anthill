"""Run a set of cases through Anthill's real answer path with a local model and store the result.

    python tools/bench/knowledge_eval/run.py --set starter --model qwen2.5:3b --model qwen3:8b

Each case gets a fresh wiki built from the case's pages (a page marked removed is written and then removed through the same code
the app uses), the question goes through ``ask_stream`` exactly as the chat does, and the answer is scored with ``score.py``. The
answer cache is off. It talks to a local Ollama, never pulls or changes a model, and reports a model that is not installed.
The result file says what it was run on (the set and its hash, the share written by someone other than the builders, the Anthill
commit, the engine and each model's digest) so two files can be compared honestly (``compare.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make the package and the repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "knowledge_eval"

from .cases import Case, load
from .score import SCORING_VERSION, score

SCHEMA = 1


def run_case(case: Case, backend, workdir: Path) -> dict:
    """One case: build its wiki, ask the question the way the chat does, time the first word, score the answer."""
    from anthill.wiki import removal
    from anthill.wiki.ask import Stage, ask_stream
    from anthill.wiki.workspace import Workspace

    ws = Workspace(workdir / case.id, scope="personal")
    ws.init()
    for slug, text in case.wiki.items():
        ws.write_page(slug, text)
    for slug in case.removed:
        removal.remove_page(ws, slug)
    record: list[dict] = []
    start = time.perf_counter()
    first_word: float | None = None
    chunks: list[str] = []
    error = ""
    try:
        for chunk in ask_stream(ws, case.question, backend, think=False, on_record=record.append):
            if isinstance(chunk, Stage):
                continue
            if first_word is None and str(chunk).strip():
                first_word = time.perf_counter() - start
            chunks.append(str(chunk))
    except Exception as why:  # noqa: BLE001 - a failed case is a result, not a crash of the whole run
        error = f"{why.__class__.__name__}: {why}"[:300]
    total = time.perf_counter() - start
    answer = "".join(chunks)
    given = [
        i.get("slug") for i in (record[0]["items"] if record else []) if i.get("kind") == "page"
    ]
    scored = score(case, answer)
    return {
        "id": case.id,
        "kind": case.kind,
        "question": case.question,
        "answer": answer,
        "passed": scored["passed"] and not error,
        "abstained": scored["abstained"],
        "missing": scored["missing"],
        "forbidden": scored["forbidden"],
        "pages_given": given,
        "first_word_ms": None if first_word is None else round(first_word * 1000),
        "total_ms": round(total * 1000),
        "error": error,
    }


def summarise(rows: list[dict], cases: list[Case]) -> dict:
    kinds: dict[str, dict] = {}
    for row in rows:
        k = kinds.setdefault(row["kind"], {"cases": 0, "passed": 0})
        k["cases"] += 1
        k["passed"] += 1 if row["passed"] else 0
    firsts = sorted(r["first_word_ms"] for r in rows if r["first_word_ms"] is not None)
    expects_answer = {c.id for c in cases if not c.abstain}
    return {
        "cases": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "pass_rate": round(sum(1 for r in rows if r["passed"]) / len(rows), 3) if rows else 0.0,
        "by_kind": kinds,
        "false_abstentions": sum(1 for r in rows if r["id"] in expects_answer and r["abstained"]),
        "errors": sum(1 for r in rows if r["error"]),
        "first_word_ms_median": int(statistics.median(firsts)) if firsts else None,
        "first_word_ms_max": firsts[-1] if firsts else None,
        "total_ms_median": int(statistics.median(r["total_ms"] for r in rows)) if rows else None,
    }


def rescore(result: dict, set_name: str | None = None) -> dict:
    """Score the stored answers of a result again with the current scorer, so a change to the scoring does not mean running the
    models again. The answers stay as they were; the passed flags, the summaries and the scoring version are recomputed."""
    cases, set_hash = load(set_name or result["set"])
    if set_hash != result.get("set_hash"):
        raise ValueError(
            "the set has changed since this result was made, so its answers cannot be scored against it"
        )
    by_id = {c.id: c for c in cases}
    for data in result["results"].values():
        for row in data["cases"]:
            scored = score(by_id[row["id"]], row["answer"])
            row.update(
                passed=scored["passed"] and not row["error"],
                abstained=scored["abstained"],
                missing=scored["missing"],
                forbidden=scored["forbidden"],
            )
        data["summary"] = summarise(data["cases"], [by_id[r["id"]] for r in data["cases"]])
    result["scoring_version"] = SCORING_VERSION
    return result


def _commit() -> str:
    try:
        root = Path(__file__).resolve().parents[3]
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run(
    set_name: str, models: list[str], *, engine, backend_for, out_dir: Path | None, limit: int = 0
) -> dict:
    """Run ``models`` over the set. ``engine`` has ``version()`` and ``require(tags)`` (digests, or an error naming what is not
    installed); ``backend_for(tag)`` gives the chat backend for a model."""
    cases, set_hash = load(set_name)
    if limit:
        cases = cases[:limit]
    digests = engine.require(models)
    os.environ["ANTHILL_DISABLE_SEMANTIC_CACHE"] = (
        "1"  # the answer cache would hide what is being measured
    )
    result: dict = {
        "schema": SCHEMA,
        "scoring_version": SCORING_VERSION,
        "set": set_name,
        "set_hash": set_hash,
        "cases": len(cases),
        "independent_cases": sum(1 for c in cases if c.independent),
        "commit": _commit(),
        "engine": engine.version(),
        "models": digests,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": {},
    }
    for tag in models:
        backend = backend_for(tag)
        with tempfile.TemporaryDirectory() as tmp:
            rows = [run_case(c, backend, Path(tmp)) for c in cases]
        result["results"][tag] = {"summary": summarise(rows, cases), "cases": rows}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{set_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["_path"] = str(path)
    return result


def _backend_for(url: str):
    from anthill.config import Config
    from anthill.inference.base import build_backend

    def make(tag: str):
        cfg = Config.from_env()
        cfg.backend, cfg.base_url, cfg.model = "ollama", url, tag
        return build_backend(cfg)

    return make


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", default="starter", help="a name in sets/ or a path to a .jsonl file")
    ap.add_argument(
        "--model",
        action="append",
        help="an installed Ollama tag; repeat for several",
    )
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--out", default="benchmarks/knowledge-eval", help="folder for the result file")
    ap.add_argument("--limit", type=int, default=0, help="only the first N cases (a quick look)")
    ap.add_argument(
        "--rescore",
        metavar="FILE",
        help="score the stored answers of a result file again; runs no model",
    )
    args = ap.parse_args(argv)
    if args.rescore:
        path = Path(args.rescore)
        path.write_text(
            json.dumps(
                rescore(json.loads(path.read_text(encoding="utf-8"))), indent=2, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"scored again with scoring version {SCORING_VERSION}: {path}")
        return 0
    if not args.model:
        ap.error("--model is required (or --rescore FILE)")
    from machinebench.engine import EngineError, OllamaEngine

    try:
        result = run(
            args.set,
            args.model,
            engine=OllamaEngine(args.url),
            backend_for=_backend_for(args.url),
            out_dir=Path(args.out),
            limit=args.limit,
        )
    except EngineError as why:
        print(f"cannot run: {why}", file=sys.stderr)
        return 2
    for tag, data in result["results"].items():
        s = data["summary"]
        print(
            f"{tag}: {s['passed']}/{s['cases']} passed, first word median {s['first_word_ms_median']} ms, false abstentions {s['false_abstentions']}"
        )
    print(
        f"independent cases: {result['independent_cases']} of {result['cases']}; written to {result['_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
