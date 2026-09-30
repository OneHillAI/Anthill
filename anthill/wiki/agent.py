from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..cache import embedder as emb
from ..common.text import strip_frontmatter
from ..inference.base import InferenceBackend
from .ingest import PdfConfirmationRequired, PdfProcessingLimitExceeded, ingest
from .lint import lint
from .workspace import Workspace


@dataclass
class AgentReport:
    ingested: list[str]  # filenames ingested from inbox/
    lint_findings: list[str]  # "kind page: detail" strings
    proposed_promotions: list[str]  # slugs ready to promote
    reuse_flags: list[str]  # slugs that are near-duplicates of org pages


def run_maintenance(
    ws: Workspace,
    backend: InferenceBackend,
    *,
    org_url: str = "",
) -> AgentReport:
    """One maintenance pass: drain inbox, lint, propose promotions, flag reuse.

    Designed to be called on a schedule (§6.3). Safe to call manually too.
    """
    report = AgentReport(ingested=[], lint_findings=[], proposed_promotions=[], reuse_flags=[])

    # ── 1. drain inbox/ ───────────────────────────────────────────────────────
    inbox_files = list(ws.inbox.glob("*"))
    for f in inbox_files:
        if f.is_file():
            try:
                ingest(ws, f, backend)
                report.ingested.append(f.name)
                f.unlink()  # remove from inbox after successful ingest
            except PdfConfirmationRequired as e:
                if f.suffix.lower() == ".pdf":
                    _set_aside_pdf(f, "needs-confirmation")
                report.lint_findings.append(f"ingest-needs-confirmation {f.name}: {e}")
            except PdfProcessingLimitExceeded as e:
                if f.suffix.lower() == ".pdf" and not e.transient:
                    _set_aside_pdf(f, "rejected")
                report.lint_findings.append(f"ingest-error {f.name}: {e}")
            except Exception as e:
                from ..multimodal.reader import PdfParseError

                if f.suffix.lower() == ".pdf" and isinstance(e, PdfParseError):
                    _set_aside_pdf(f, "rejected")
                report.lint_findings.append(f"ingest-error {f.name}: {e}")

    # ── 2. lint ───────────────────────────────────────────────────────────────
    for finding in lint(ws):
        report.lint_findings.append(f"{finding.kind} {finding.page}: {finding.detail}")

    # ── 3. propose promotions + flag reuse ───────────────────────────────────
    if org_url:
        _propose_and_flag(ws, org_url, report)

    return report


def _set_aside_pdf(source, state: str) -> None:
    target_dir = source.parent / state
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        suffix = 1
        while target.exists():
            target = target_dir / f"{source.stem}-{suffix}{source.suffix}"
            suffix += 1
        source.rename(target)
    except OSError:
        return


def _propose_and_flag(ws: Workspace, org_url: str, report: AgentReport) -> None:
    """Compare local wiki pages against the org wiki.

    - If a local page has no match in the org wiki → propose it for promotion.
    - If a local page is semantically close to an org page → flag as potential
      reuse (so the user doesn't do work the org already did).
    """
    try:
        resp = httpx.get(f"{org_url.rstrip('/')}/wiki/pages", timeout=10)
        resp.raise_for_status()
        org_pages = {p["slug"] for p in resp.json()}
    except httpx.HTTPError:
        return

    for local_page in ws.pages():
        slug = local_page.stem
        if slug in org_pages:
            # Already in the org wiki; check for reuse opportunity on newer content.
            continue

        # New page not in org wiki → candidate for promotion.
        report.proposed_promotions.append(slug)

    # Flag near-duplicate local pages (possible redundant work).
    local_pages = ws.pages()
    if len(local_pages) < 2:
        return
    try:
        vecs = [(p, emb.embed(strip_frontmatter(p.read_text())[:2000])) for p in local_pages]
    except Exception:
        return

    seen: set[str] = set()
    for i, (p_a, v_a) in enumerate(vecs):
        for p_b, v_b in vecs[i + 1 :]:
            if p_a.stem in seen or p_b.stem in seen:
                continue
            if emb.cosine(v_a, v_b) >= 0.92:
                report.reuse_flags.append(
                    f"{p_a.stem} ≈ {p_b.stem} (high similarity - review for overlap)"
                )
                seen.add(p_b.stem)
