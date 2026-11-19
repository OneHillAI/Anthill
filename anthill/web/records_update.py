"""The one-time "Update records" step for an install that has kept originals with no record (#277). Spec:
docs/specs/only-approved-items-reach-answers.md.

Records are made as pages are saved and as originals are uploaded; an install that existed before them has
originals with none. Until an original has a record, an answer reads it as it always did, so an old upload that
was waiting or rejected would still be read. Nothing is changed on its own: the owner is told once, and the step
is one button. It runs exactly what ``anthill knowledge inventory --apply`` runs, with the same backup and undo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import Engine

from ..wiki import inventory as inv
from ..wiki import items
from ..wiki.page_index import workspace_roots
from ..wiki.workspace import space_of_root, workspace_at


def review_status_for(database: Engine | Path | str | None, root: Path) -> dict[str, str] | None:
    """Stored-original name -> review status, for the pages proposed into the workspace at ``root``, read from
    the database (an engine, or the path of its file). None when there is no database or it cannot be read, or
    for the legacy personal wiki, whose reviews cannot be attributed to it."""
    space, ref = space_of_root(root)
    if database is None or (not isinstance(database, Engine) and not Path(database).is_file()):
        return None
    if space == "personal" and not ref:
        return None
    try:
        from sqlalchemy.orm import sessionmaker

        from . import db as db_mod

        engine = database if isinstance(database, Engine) else db_mod.get_engine(Path(database))
        session = sessionmaker(bind=engine)()
        try:
            q = session.query(db_mod.WikiReview).filter(db_mod.WikiReview.kind == "page")
            num = int(ref.split("-", 1)[1]) if ref else None
            if space == "team" and num is not None:
                q = q.filter(
                    db_mod.WikiReview.target_scope == "team", db_mod.WikiReview.team_id == num
                )
            elif space == "personal" and num is not None:
                q = q.filter(
                    db_mod.WikiReview.target_scope == "personal",
                    db_mod.WikiReview.proposed_by == num,
                )
            elif space == "org":
                q = q.filter(db_mod.WikiReview.target_scope == "org")
                if num is not None:
                    q = q.filter(db_mod.WikiReview.org_id == num)
            else:
                return None
            out: dict[str, str] = {}
            for row in q.order_by(db_mod.WikiReview.id):
                name = inv.provenance_source(str(row.content or ""))
                if name:
                    out[name] = str(row.status)  # a later row for the same original wins
            return out
        finally:
            session.close()
    except Exception:  # noqa: BLE001 - an unreadable database only means leftovers cannot be matched
        return None


def _marker() -> Path:
    return Path(os.environ.get("ANTHILL_WIKI_ROOT", "data/wikis")) / ".records-notice-dismissed"


def roots_without_records() -> list[Path]:
    """The wiki folders on this install that hold a kept original with no record. Reads folder listings only."""
    out = []
    for root in workspace_roots():
        raw = root / "raw"
        if not raw.is_dir():
            continue
        sidecars = root / items.RECORDS_DIR / items.SOURCES_DIR
        for p in raw.iterdir():
            if (
                p.is_file()
                and not p.name.startswith(".")
                and not (sidecars / f"{p.name}.json").exists()
            ):
                out.append(root)
                break
    return out


def notice_due() -> bool:
    """Show the one-time notice: some original has no record, and it has not been dismissed."""
    return not _marker().exists() and bool(roots_without_records())


def dismiss_notice() -> None:
    marker = _marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("dismissed\n", encoding="utf-8")


@dataclass
class UpdateSummary:
    folders: int = 0
    pages: int = 0
    originals: int = 0
    backups: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def update_records(database: Engine | Path | str | None) -> UpdateSummary:
    """Add the missing records to every wiki on the install: the same step as ``anthill knowledge inventory
    --apply``. A backup is made for each folder first; a folder whose backup fails is left alone and reported."""
    summary = UpdateSummary()
    for root in workspace_roots():
        ws = workspace_at(root)
        try:
            res = inv.apply_records(ws, review_status=review_status_for(database, root))
        except OSError as exc:
            summary.skipped.append(f"{root}: {exc}")
            continue
        if res.nothing_to_do:
            continue
        summary.folders += 1
        summary.pages += res.changed_pages
        summary.originals += res.changed_sources
        summary.backups.append(res.backup)
        summary.manifests.append(res.manifest)
        summary.skipped.extend(res.skipped)
    dismiss_notice()  # done: nothing is left to tell anyone about
    return summary
