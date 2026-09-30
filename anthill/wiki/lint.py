from __future__ import annotations

from dataclasses import dataclass

from ..common.text import outbound_links, slugify
from .workspace import Workspace


@dataclass
class Finding:
    kind: str  # "broken-link" | "orphan" | "empty"
    page: str
    detail: str


def lint(ws: Workspace) -> list[Finding]:
    """Mechanical consistency checks that need no model: broken links, orphan pages,
    empty pages. Model-judged checks (contradictions, stale claims) come in Phase 2."""
    pages = ws.pages()
    slugs = {p.stem for p in pages}
    inbound = dict.fromkeys(slugs, 0)
    findings: list[Finding] = []

    for p in pages:
        text = p.read_text()
        if not text.strip():
            findings.append(Finding("empty", p.stem, "page has no content"))
        for target in outbound_links(text):
            slug = slugify(target)
            if slug in inbound:
                inbound[slug] += 1
            else:
                findings.append(
                    Finding("broken-link", p.stem, f"links to missing page [[{target}]]")
                )

    if len(pages) > 1:
        for slug, count in inbound.items():
            if count == 0:
                findings.append(Finding("orphan", slug, "no other page links here"))

    return findings
