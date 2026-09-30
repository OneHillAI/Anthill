"""Phase 7 of #683: a thin DB registry (index) over wiki pages, skills, and principles documents.

Spec requirement 6 ("Storage: markdown canonical, plus a database ledger"): wiki/skill CONTENT stays
markdown files on disk - the sovereignty, portability, and OKGF-export properties are load-bearing and
this module never touches them. It only maintains `KnowledgeItem` (`anthill/web/db.py`), an additive
index of what currently exists. This is a different job from `WikiReview`/`ProposedSkill`, which cover
a change AWAITING approval, not a durable "what's live" registry - and it stays purely additive: no
other route's behavior changes because this module exists.

Hooked into as few choke points as possible rather than every route:

- `propose_wiki_write()`'s auto-apply branch (`anthill/web/app.py`) - the single funnel every wiki-page
  write already goes through (upload, connector import, research topics, snippet-to-wiki, and any
  sibling phase's snippet-bridge work) - calls `sync_page()`.
- `write_skill()` (`anthill/agent/skills.py`) - every skill write, personal or review-gated - calls
  `sync_skill()`.
- `approve_review()`'s per-kind branches (`anthill/web/app.py`) - a page/skill/principles change
  approved through the review gate - calls the matching `sync_*()`.
- `skills_delete()` (`anthill/web/app.py`) - calls `remove()`.

No other route needs to change for the registry to stay accurate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .db import KnowledgeItem


def _find(
    db: Session, *, org_id: int, scope: str, team_id: int | None, kind: str, slug: str
) -> KnowledgeItem | None:
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.org_id == org_id,
            KnowledgeItem.scope == scope,
            KnowledgeItem.team_id == team_id,
            KnowledgeItem.kind == kind,
            KnowledgeItem.slug == slug,
        )
        .first()
    )


def _upsert(
    db: Session,
    *,
    org_id: int,
    scope: str,
    team_id: int | None,
    kind: str,
    slug: str,
    title: str,
    path: str,
    user_id: int | None,
    review_state: str,
) -> KnowledgeItem:
    row = _find(db, org_id=org_id, scope=scope, team_id=team_id, kind=kind, slug=slug)
    now = datetime.now(timezone.utc)
    if row is None:
        row = KnowledgeItem(
            org_id=org_id,
            scope=scope,
            team_id=team_id,
            kind=kind,
            slug=slug,
            title=title,
            path=path,
            review_state=review_state,
            created_at=now,
            updated_at=now,
            last_editor_id=user_id,
        )
        db.add(row)
    else:
        row.title = title
        row.path = path
        row.review_state = review_state
        row.updated_at = now
        row.last_editor_id = user_id
    db.commit()
    db.refresh(row)
    return row


def sync_page(
    db: Session,
    *,
    org_id: int,
    scope: str,
    team_id: int | None,
    slug: str,
    title: str,
    path: str,
    user_id: int | None,
    review_state: str = "approved",
) -> KnowledgeItem:
    """Upsert the registry row for a wiki page (kind='page'). Call right after the page is actually
    written to disk - never speculatively."""
    return _upsert(
        db,
        org_id=org_id,
        scope=scope,
        team_id=team_id,
        kind="page",
        slug=slug,
        title=title,
        path=path,
        user_id=user_id,
        review_state=review_state,
    )


def sync_skill(
    db: Session,
    *,
    org_id: int,
    scope: str,
    team_id: int | None,
    slug: str,
    title: str,
    path: str,
    user_id: int | None,
    review_state: str = "approved",
) -> KnowledgeItem:
    """Upsert the registry row for a skill (kind='skill'). See `sync_page`."""
    return _upsert(
        db,
        org_id=org_id,
        scope=scope,
        team_id=team_id,
        kind="skill",
        slug=slug,
        title=title,
        path=path,
        user_id=user_id,
        review_state=review_state,
    )


def sync_principles(
    db: Session,
    *,
    org_id: int,
    scope: str,
    team_id: int | None,
    path: str,
    user_id: int | None,
    review_state: str = "approved",
) -> KnowledgeItem:
    """Upsert the registry row for a scope's principles document (kind='principles'). There is exactly
    one per scope, so the slug is the fixed sentinel `PRINCIPLES` (matches `Workspace.principles_md`,
    a single file, not a directory of many)."""
    return _upsert(
        db,
        org_id=org_id,
        scope=scope,
        team_id=team_id,
        kind="principles",
        slug="PRINCIPLES",
        title="Principles",
        path=path,
        user_id=user_id,
        review_state=review_state,
    )


def remove(
    db: Session, *, org_id: int, scope: str, team_id: int | None, kind: str, slug: str
) -> None:
    """Delete the registry row for a page/skill/principles doc that no longer exists (e.g.
    `skills_delete()`). A no-op if it was never registered (a builtin skill, which has no org and is
    never registered; or content that predates the registry and hasn't been backfilled yet)."""
    db.query(KnowledgeItem).filter(
        KnowledgeItem.org_id == org_id,
        KnowledgeItem.scope == scope,
        KnowledgeItem.team_id == team_id,
        KnowledgeItem.kind == kind,
        KnowledgeItem.slug == slug,
    ).delete()
    db.commit()


def backfill_workspace(
    db: Session,
    ws,
    *,
    org_id: int,
    scope: str,
    team_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """One-time-per-item, idempotent backfill: register any page/skill in workspace `ws` that isn't
    already a `KnowledgeItem` row. Only ever INSERTS a missing row - an already-registered item (kept
    current by the live `sync_*` hooks above) is left untouched, so this is cheap and safe to run on
    every boot (a set of existence checks, not a rewrite), unlike a versioned schema migration. Returns
    the number of rows added. Never touches the files themselves."""
    from ..common.text import first_h1

    added = 0
    for p in ws.pages():
        slug = p.stem
        if _find(db, org_id=org_id, scope=scope, team_id=team_id, kind="page", slug=slug):
            continue
        text = p.read_text(encoding="utf-8")
        sync_page(
            db,
            org_id=org_id,
            scope=scope,
            team_id=team_id,
            slug=slug,
            title=first_h1(text) or slug,
            path=str(p),
            user_id=user_id,
        )
        added += 1

    if ws.skills.is_dir():
        from ..agent.skills import parse_skill_md

        for folder in sorted(ws.skills.iterdir()):
            if not folder.is_dir():
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.exists():
                continue
            slug = folder.name
            if _find(db, org_id=org_id, scope=scope, team_id=team_id, kind="skill", slug=slug):
                continue
            sk = parse_skill_md(
                skill_file.read_text(encoding="utf-8"), slug=slug, path=str(skill_file)
            )
            sync_skill(
                db,
                org_id=org_id,
                scope=scope,
                team_id=team_id,
                slug=slug,
                title=sk.name or slug,
                path=str(skill_file),
                user_id=user_id,
            )
            added += 1
    return added
