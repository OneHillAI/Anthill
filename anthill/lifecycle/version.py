from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote

# Provenance is a trailing HTML comment so it never disturbs the H1/summary/Related
# structure the index and normalizer read from the top of the page.
PROVENANCE_RE = re.compile(
    r"<!--\s*anthill:\s*model=(?P<model>\S+)\s+ingested=(?P<date>\S+)"
    r"(?:\s+pipeline=(?P<pipeline>\S+))?"
    r"(?:\s+source=(?P<source>\S+))?\s*-->"
)


def tag_page(
    content: str,
    model: str,
    *,
    on: str | None = None,
    source: str | None = None,
    pipeline_model: str | None = None,
) -> str:
    """Record the writer, optional configured pipeline model, and immutable source."""
    stamped = on or date.today().isoformat()
    existing = PROVENANCE_RE.search(content)
    if source is None and existing and existing.group("source"):
        source = unquote(existing.group("source"))
    if pipeline_model is None and existing and existing.group("pipeline"):
        pipeline_model = existing.group("pipeline")
    fields = [f"model={model}", f"ingested={stamped}"]
    if pipeline_model:
        fields.append(f"pipeline={pipeline_model}")
    if source:
        fields.append(f"source={quote(source, safe='')}")
    comment = f"<!-- anthill: {' '.join(fields)} -->"
    stripped = PROVENANCE_RE.sub("", content).rstrip()
    return f"{stripped}\n\n{comment}\n"


def read_provenance(content: str) -> dict | None:
    """Return the fields from a page's provenance tag, if present."""
    m = PROVENANCE_RE.search(content)
    if not m:
        return None
    provenance = {"model": m.group("model"), "date": m.group("date")}
    if m.group("pipeline"):
        provenance["pipeline_model"] = m.group("pipeline")
    if m.group("source"):
        provenance["source"] = unquote(m.group("source"))
    return provenance


def stale_pages(wiki_dir: Path, current_model: str) -> list[tuple[Path, str | None]]:
    """Pages whose configured pipeline model differs from ``current_model``.

    Older tags without a pipeline field fall back to their writer model. Untagged
    pages count as stale because their authoring pipeline is unknown.
    """
    out: list[tuple[Path, str | None]] = []
    for p in sorted(wiki_dir.glob("*.md")):
        prov = read_provenance(p.read_text())
        recorded = (prov.get("pipeline_model") or prov["model"]) if prov else None
        if recorded != current_model:
            out.append((p, recorded))
    return out
