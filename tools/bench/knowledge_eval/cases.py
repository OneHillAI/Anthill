"""Load and check a set of evaluation cases (one JSON object per line). A case is a small wiki, a question and what a correct
answer must and must not say. ``independent`` says whether someone other than the builders wrote the documents and the question."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("lookup", "update", "correction", "conflict", "deletion", "unanswerable", "multi_source")
SETS_DIR = Path(__file__).parent / "sets"


class CaseError(ValueError):
    pass


@dataclass
class Case:
    id: str
    kind: str
    question: str
    wiki: dict[str, str]
    must_include: list[list[str]] = field(
        default_factory=list
    )  # every group needs one of its alternatives
    must_not_include: list[str] = field(default_factory=list)
    abstain: bool = False  # the right answer is to say it is not in the knowledge base
    removed: list[str] = field(
        default_factory=list
    )  # pages written, then removed, before the question
    author: str = "builders"
    independent: bool = False


def _check(raw: dict, where: str) -> Case:
    try:
        case = Case(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            question=str(raw["question"]),
            wiki={str(k): str(v) for k, v in raw["wiki"].items()},
            must_include=[[str(a) for a in g] for g in raw.get("must_include", [])],
            must_not_include=[str(s) for s in raw.get("must_not_include", [])],
            abstain=bool(raw.get("abstain", False)),
            removed=[str(s) for s in raw.get("removed", [])],
            author=str(raw.get("author", "builders")),
            independent=bool(raw.get("independent", False)),
        )
    except (KeyError, TypeError, AttributeError) as why:
        raise CaseError(f"{where}: a case needs id, kind, question and wiki ({why!r})") from why
    if case.kind not in KINDS:
        raise CaseError(f"{where}: unknown kind {case.kind!r} (one of {', '.join(KINDS)})")
    if not case.abstain and not case.must_include:
        raise CaseError(f"{where}: {case.id} has no must_include and does not expect an abstention")
    if any(not g for g in case.must_include):
        raise CaseError(f"{where}: {case.id} has an empty must_include group")
    for slug in case.removed:
        if slug not in case.wiki:
            raise CaseError(f"{where}: {case.id} removes {slug!r}, which is not in its wiki")
    return case


def load(name_or_path: str) -> tuple[list[Case], str]:
    """The cases of a set (a name in ``sets/`` or a path) and a hash of its file, so a result says what it was run on."""
    path = Path(name_or_path)
    if not path.is_file():
        path = SETS_DIR / f"{name_or_path}.jsonl"
    if not path.is_file():
        raise CaseError(f"no set {name_or_path!r} (looked for {path})")
    data = path.read_bytes()
    cases, seen = [], set()
    for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as why:
            raise CaseError(f"{path.name}:{n}: not JSON ({why})") from why
        case = _check(raw, f"{path.name}:{n}")
        if case.id in seen:
            raise CaseError(f"{path.name}:{n}: duplicate id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise CaseError(f"{path.name} has no cases")
    return cases, hashlib.sha256(data).hexdigest()[:16]
