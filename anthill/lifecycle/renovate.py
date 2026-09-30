from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..common.text import first_h1, normalize_wiki_page
from ..inference.base import InferenceBackend
from ..wiki.ingest import (
    PdfConfirmationRequired,
    PdfProcessingLimitExceeded,
    source_to_page,
)
from ..wiki.workspace import Workspace
from .version import read_provenance, stale_pages, tag_page


@dataclass
class RenovationReport:
    new_model: str
    regenerated: list[str] = field(default_factory=list)  # slugs rewritten
    skipped: list[str] = field(default_factory=list)  # slugs with no source in raw/
    unchanged: list[str] = field(default_factory=list)  # already current model

    def summary(self) -> str:
        return (
            f"renovated {len(self.regenerated)} page(s) with {self.new_model}; "
            f"{len(self.skipped)} skipped (no source), "
            f"{len(self.unchanged)} already current"
        )


def renovate(
    ws: Workspace,
    backend: InferenceBackend,
    new_model: str,
    *,
    only_stale: bool = True,
    dry_run: bool = False,
    allow_large_pdf: bool = False,
) -> RenovationReport:
    """Regenerate wiki pages by re-ingesting their raw source through a new model.

    The raw/ sources are immutable, so a page can always be rebuilt from them.
    Pages whose source is no longer in raw/ are left untouched (the model can't
    recreate them faithfully) and reported as skipped.

    only_stale=True (default) renovates only pages NOT tagged with new_model.
    dry_run=True reports what would change without writing.
    """
    report = RenovationReport(new_model=new_model)
    targets = stale_pages(ws.wiki, new_model) if only_stale else [(p, None) for p in ws.pages()]

    raw_by_stem = {p.stem: p for p in ws.raw.glob("*")}

    for page_path, recorded in targets:
        slug = page_path.stem
        if recorded == new_model:
            report.unchanged.append(slug)
            continue

        # Find the original source. Pages are titled from the source H1, so the
        # slug may differ from the source filename - match on the raw copy whose
        # name stem slugifies to this page, else fall back to the page's own raw file.
        source = _match_source(page_path, raw_by_stem, ws)
        if source is None:
            report.skipped.append(slug)
            continue

        if dry_run:
            report.regenerated.append(slug)
            continue

        try:
            page_md, writer_model = source_to_page(
                ws,
                source,
                backend,
                model=new_model,
                allow_large_pdf=allow_large_pdf,
            )
        except (PdfConfirmationRequired, PdfProcessingLimitExceeded) as exc:
            reason = str(exc) or "transient processing limit"
            report.skipped.append(f"{slug}: {reason}")
            continue
        page_md = tag_page(
            normalize_wiki_page(page_md),
            writer_model or new_model,
            source=source.name,
            pipeline_model=new_model,
        )
        title = first_h1(page_md) or slug
        ws.write_page(title, page_md)
        report.regenerated.append(slug)

    if not dry_run and report.regenerated:
        ws.rebuild_index()
        ws.append_log("renovate", f"{len(report.regenerated)} pages → {new_model}")
    return report


def _match_source(page_path: Path, raw_by_stem: dict[str, Path], ws: Workspace) -> Path | None:
    from ..common.text import slugify

    provenance = read_provenance(page_path.read_text())
    source_name = provenance.get("source") if provenance else None
    if source_name:
        source = ws.raw / source_name
        if source.name == source_name and source.is_file():
            return source

    slug = page_path.stem
    # Exact stem match first, then slugified-stem match.
    if slug in raw_by_stem:
        return raw_by_stem[slug]
    for stem, path in raw_by_stem.items():
        if slugify(stem) == slug:
            return path
    return None
