"""The status of the review row that proposed each kept original, for the inventory (#272) and for nothing else.

The command ``anthill knowledge inventory`` uses it to tell an original left over from a held or rejected upload from
any other original with no page. It lives under ``web`` because it reads the database, which the wiki package may not
import."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from ..wiki import inventory as inv
from ..wiki.workspace import space_of_root


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
