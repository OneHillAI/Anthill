"""Inbound Wiki Review Gate Bridge (Sec 4.4, Rule 4).

Incoming peer ``PageChange`` records NEVER write directly to the user's wiki. Every inbound
page is evaluated with ``anthill.wiki.review.outline_change`` and classified into one of:
  - ``proposed``: default for trusted peers and new non-conflicting pages.
  - ``contested``: local page with the same slug exists with different body (kept side-by-side,
    no auto-merge).
  - ``quarantined``: low-trust Open Hill record (excluded from AI retrieval until verified/admitted).

When a human accepts a proposal, the page is written via ``Workspace.write_page(..., review="approved")``
and the local search index is rebuilt from accepted pages only (vectors never travel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.text import first_h1, slugify, strip_frontmatter
from ..wiki import review as wiki_review
from ..wiki.workspace import Workspace
from . import DEFAULT_OPEN_TRUST_THRESHOLD


@dataclass
class InboundWikiProposal:
    slug: str
    title: str
    peer_state: str  # "proposed" | "contested" | "quarantined"
    content: str
    local_content: str
    outline: str
    flags: list[str] = field(default_factory=list)
    diff_summary: str = ""
    hill_id: str = ""
    record_hash: str = ""
    signer_root_key: str = ""
    signer_name: str = ""


def evaluate_inbound_page_change(
    ws: Workspace,
    *,
    record: dict[str, Any],
    content: str,
    signer_name: str,
    signer_karma: int = 0,
    open_trust_threshold: int = DEFAULT_OPEN_TRUST_THRESHOLD,
) -> InboundWikiProposal:
    """Evaluate a verified peer ``PageChange`` without modifying the local workspace."""
    payload = record.get("payload") or {}
    raw_slug = str(payload.get("slug") or first_h1(content) or "peer-page")
    slug = slugify(raw_slug)
    title = str(payload.get("title") or first_h1(content) or slug)
    scope = str(record.get("scope") or "private-group")
    hill_id = str(record.get("hill_id") or "")
    rec_hash = str(record.get("record_hash") or "")
    signer_key = str(record.get("author_root_key") or "")

    # Mechanical review pass only (no auto-apply, no model call required).
    mechanical = wiki_review.outline_change(None, ws, slug, content, scope="personal")
    flags = list(mechanical.flags)

    local_path = ws.wiki / f"{slug}.md"
    local_content = ""
    has_conflict = False
    if local_path.exists():
        try:
            local_raw = local_path.read_text(encoding="utf-8")
            local_content = strip_frontmatter(local_raw).strip()
            incoming_body = strip_frontmatter(content).strip()
            has_conflict = bool(local_content) and local_content != incoming_body
        except OSError:
            local_content = ""

    if scope == "open" and signer_karma < open_trust_threshold:
        peer_state = "quarantined"
        if "peer_quarantined" not in flags:
            flags.append("peer_quarantined")
        state_note = (
            f"Quarantined inbound Open Hill page from {signer_name} (karma {signer_karma} < "
            f"threshold {open_trust_threshold}). Excluded from AI retrieval until verified and admitted."
        )
    elif has_conflict:
        peer_state = "contested"
        for f in ("peer_contested", "new_version"):
            if f not in flags:
                flags.append(f)
        state_note = (
            f"Contested edit from {signer_name}: conflicts with your local page '{slug}'. "
            "Both signed versions are kept side-by-side (no auto-merge)."
        )
    else:
        peer_state = "proposed"
        if "peer_proposed" not in flags:
            flags.append("peer_proposed")
        state_note = (
            f"Proposed peer page from {signer_name} (signed seq #{record.get('seq')}). "
            "Rule 4: never written directly to your wiki until approved."
        )

    outline = f"{state_note}\n{mechanical.text}".strip()
    diff_summary = (
        f"Peer {peer_state} from {signer_name} (Hill: {hill_id}, seq #{record.get('seq')})"
    )

    return InboundWikiProposal(
        slug=slug,
        title=title,
        peer_state=peer_state,
        content=content,
        local_content=local_content,
        outline=outline,
        flags=flags,
        diff_summary=diff_summary,
        hill_id=hill_id,
        record_hash=rec_hash,
        signer_root_key=signer_key,
        signer_name=signer_name,
    )


def accept_peer_page(
    ws: Workspace, *, slug: str, content: str, sources: list[str] | None = None
) -> Path:
    """Write an accepted peer proposal into the local wiki and rebuild the local search index."""
    if not ws.exists():
        ws.init()
    path = ws.write_page(slug, content, review="approved", sources=sources)
    ws.rebuild_index()
    ws.append_log("peer-accept", slug)
    return path
