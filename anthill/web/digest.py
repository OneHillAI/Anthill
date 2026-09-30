"""Phase 7 of #683: the knowledge digest (spec requirement 5's digest half).

Summarizes what changed in an org's knowledge surfaces between two points in time, built entirely from
`AuditLog` rows - no separate "digest data" table, per the spec's own framing that the audit log
already has everything needed once it is actually comprehensive (Phase 6, `683-knowledge-audit-
coverage`, closed the real gaps in that coverage).

The event prefixes below are the corrected, VERIFIED set from Phase 6, not the spec's original ("only
`wiki.rejected` and `memory.to_wiki` are logged") framing: `wiki.*`, `skill.*`, `principles.*`,
`snippet.*` cover every wiki/skill/principles/snippet mutation (create, edit, delete, upload, import,
approve, reject - see Phase 6's commit for the full enumeration). `memory.to_wiki` is included because
it is a promotion of memory content INTO the wiki, i.e. wiki-surface content by the time it lands -
`memory.add`/`memory.edit`/`memory.delete` are plain memory-item CRUD and deliberately excluded (they
are the Memory surface's own concern, not "what changed in the knowledge base" this digest reports on).
`memory.promote`/`memory.promote_team` (moving a memory item between personal/team/org scope without
ever becoming a wiki page) ARE included under "promotions", since the spec's own acceptance criterion
explicitly asks for "what was promoted between scopes".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .db import AuditLog

# Prefixes matched with a plain str.startswith - AuditLog.event values are short, fixed literals
# (see db.py's AuditLog.event docstring), so this is unambiguous (no "wiki.approved" vs. "wiki2.x").
_WIKI_SKILL_PREFIXES = ("wiki.", "skill.", "principles.", "snippet.")
_MEMORY_EVENTS = ("memory.to_wiki", "memory.promote", "memory.promote_team")


def _is_knowledge_event(event: str) -> bool:
    return event.startswith(_WIKI_SKILL_PREFIXES) or event in _MEMORY_EVENTS


@dataclass
class DigestSummary:
    """A simple structured summary of an org's knowledge-mutation activity in a window. Counts are
    bucketed by audit event name, with one narrow exception: `wiki.upload`/`wiki.upload.confirm`/
    `wiki.import_connector`/`wiki.import_okgf` log the SAME event name for both a successful write and
    a failed attempt (Phase 6 did not change this), distinguished only by an `error=` marker in the
    free-text `detail` - so counting only genuinely-changed pages needs that one check. Every other
    bucket is pure event-name matching, robust to detail-string wording changes."""

    org_id: int
    since: datetime
    until: datetime
    pages_changed: int = 0  # wiki.approved + a successful wiki.upload*/wiki.import_*
    pages_rejected: int = 0  # wiki.rejected
    skills_learned: int = 0  # skill.created + skill.approved (hand-authored or approved via review)
    skills_adopted: int = 0  # skill.proposal_accepted (accepted from auto-distillation)
    skills_deleted: int = 0  # skill.deleted
    skills_rejected: int = 0  # skill.rejected + skill.proposal_rejected
    principles_changed: int = 0  # principles.approved
    principles_rejected: int = 0  # principles.rejected
    snippets_captured: int = 0  # snippet.saved
    promotions: int = 0  # snippet.to_wiki, memory.to_wiki, memory.promote, memory.promote_team
    total_events: int = 0
    events: list[AuditLog] = field(default_factory=list)  # the raw rows, for a detailed view

    @property
    def is_empty(self) -> bool:
        return self.total_events == 0


def build_digest(
    db: Session, org_id: int, since: datetime, *, until: datetime | None = None
) -> DigestSummary:
    """Summarize `org_id`'s knowledge-mutation audit events in [`since`, `until`) (`until` defaults to
    now). Read-only - never mutates the audit log or anything else."""
    until = until or datetime.now(timezone.utc)
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.org_id == org_id,
            AuditLog.created_at >= since,
            AuditLog.created_at < until,
        )
        .order_by(AuditLog.created_at.asc())
        .all()
    )
    rows = [r for r in rows if _is_knowledge_event(str(r.event))]

    summary = DigestSummary(org_id=org_id, since=since, until=until, events=rows)
    for r in rows:
        e = r.event
        if e == "wiki.approved" or (
            e.startswith(("wiki.upload", "wiki.import_")) and "error=" not in r.detail
        ):
            summary.pages_changed += 1
        elif e == "wiki.rejected":
            summary.pages_rejected += 1
        elif e in ("skill.created", "skill.approved"):
            summary.skills_learned += 1
        elif e == "skill.proposal_accepted":
            summary.skills_adopted += 1
        elif e == "skill.deleted":
            summary.skills_deleted += 1
        elif e in ("skill.rejected", "skill.proposal_rejected"):
            summary.skills_rejected += 1
        elif e == "principles.approved":
            summary.principles_changed += 1
        elif e == "principles.rejected":
            summary.principles_rejected += 1
        elif e == "snippet.saved":
            summary.snippets_captured += 1
        elif e in ("snippet.to_wiki", "memory.to_wiki", "memory.promote", "memory.promote_team"):
            summary.promotions += 1
    summary.total_events = len(rows)
    return summary
