"""The paths and links the contributor docs point at exist.

AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md, .asdd.yml and docs/*.md name files in the repo. After a rename or
a delete those sentences go stale, and today only the post-merge documentation agent can notice. This checks
relative markdown links and backticked repo paths (`anthill/...`, `docs/...`, `.github/...` and so on).

docs/SYSTEM_IMPACT_LOG.md is skipped: it is a history of past changes and rightly names files that no longer
exist.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    ".asdd.yml",
    *sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "docs").glob("*.md")
        if p.name != "SYSTEM_IMPACT_LOG.md"
    ),
]

LINK = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
REPO_PATH = re.compile(
    r"`((?:anthill|scripts|cli|docs|\.github|tests|src-tauri|tools|recipes|skills)/[A-Za-z0-9_./-]+)`"
)
FENCE = re.compile(r"```.*?```", re.S)

# Reviewed false positives and known-stale lines: (file, reference) -> why. Never add one to land a change;
# fix the reference. Remove an entry when its reference is fixed (the test fails on a stale entry).
KNOWN = {
    (
        "ARCHITECTURE.md",
        "docs/walkthrough.md",
    ): "roadmap line for the v0.1.0 tag; the file does not exist",
    ("docs/asdd-goose-adoption.md", "cli/README.md"): "a path in the ASDD repo, not this one",
}


def _references(rel: str) -> list[tuple[str, Path]]:
    text = FENCE.sub("", (ROOT / rel).read_text())
    refs = []
    for m in LINK.finditer(text):
        target = m[1].split("#")[0]
        if target and not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not target.startswith("/"):
            refs.append((m[1], (ROOT / rel).parent / target))
    external_links_removed = LINK.sub(lambda m: "" if re.match(r"^[a-z]+:", m[1]) else m[0], text)
    for m in REPO_PATH.finditer(external_links_removed):
        if "*" not in m[1]:
            refs.append((m[1], ROOT / m[1].rstrip(".")))
    return refs


def _broken() -> dict[tuple[str, str], int]:
    broken: dict[tuple[str, str], int] = {}
    for rel in FILES:
        for shown, path in _references(rel):
            if not path.exists():
                broken[(rel, shown)] = broken.get((rel, shown), 0) + 1
    return broken


def test_doc_files_were_found():
    assert len(FILES) >= 20 and all((ROOT / f).exists() for f in FILES)


def test_doc_references_exist():
    new = sorted(k for k in _broken() if k not in KNOWN)
    assert not new, "docs point at files that do not exist (renamed or deleted?): " + "; ".join(
        f"{f}: {ref}" for f, ref in new
    )


def test_known_stale_list_has_no_fixed_entries():
    fixed = sorted(k for k in KNOWN if k not in _broken())
    assert not fixed, f"reference now resolves or is gone; drop it from KNOWN: {fixed}"
