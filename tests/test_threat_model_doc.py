"""docs/THREAT_MODEL.md must not drift from the repo (spec: docs/specs/privacy-threat-model.md).

Model-free and offline: it only reads the document and checks the paths and test names it cites.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "THREAT_MODEL.md"

# A path the document names in backticks: a repo path with a known top folder and a file extension.
_PATH_RE = re.compile(
    r"`((?:anthill|tests|docs|src-tauri|\.github|scripts)/[\w./-]+\.\w+)(?:::(\w+))?`"
)
_ALLOWED_TEST_CELL = ("no test yet", "intended; not yet verified")


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_document_exists_and_names_the_commit_it_was_read_at() -> None:
    text = _text()
    assert re.search(r"commit `[0-9a-f]{7,40}`", text), "state the commit the code was read at"
    assert "Nothing was run" in text, "say that the code was read, not run"


def test_every_cited_path_exists() -> None:
    missing = []
    for rel, name in _PATH_RE.findall(_text()):
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
        elif name and f"def {name}" not in path.read_text(encoding="utf-8"):
            missing.append(f"{rel}::{name}")
    assert not missing, (
        f"THREAT_MODEL.md cites files or tests that do not exist: {sorted(set(missing))}"
    )


def test_every_row_in_section_6_is_tested_or_says_why_not() -> None:
    section = _section(_text(), "## 6. What stops each attacker")
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("| A") and not line.startswith("| Attacker")
    ]
    assert len(rows) >= 10, "section 6 should have a row per attacker per shape"
    bad = []
    for line in rows:
        last_cell = [c.strip() for c in line.strip().strip("|").split("|")][-1]
        cites_a_test = bool(re.search(r"`tests/[\w./-]+\.py`", last_cell))
        says_why_not = any(token in last_cell.lower() for token in _ALLOWED_TEST_CELL)
        if not (cites_a_test or says_why_not):
            bad.append(line[:80])
    assert not bad, f"rows with no test and no reason: {bad}"


def test_the_three_install_shapes_and_the_scope_limits_are_stated() -> None:
    text = _text()
    for heading in ("### 6.1 Solo", "### 6.2 Headless or LAN", "### 6.3 Org"):
        assert heading in text, f"missing {heading}"
    assert "## 5. Out of scope" in text
    assert "## 7. Every way data leaves a node" in text
    assert "## 8. Not claimed in this version" in text
