"""Archive a person's own memory rows once (docs/specs/memory-archive.md). Reads memory; never changes or deletes a row."""

from __future__ import annotations

import logging

from ..wiki import memory_archive
from .db import MemoryItem

log = logging.getLogger(__name__)


def ensure_archive(db, user_id: int, ws) -> bool:
    """Write the archive of the person's personal memory rows the first time it is needed and their wiki exists. True when an archive was
    written now. The marker is checked before the database is read. Never raises: a failure is logged and tried again next time."""
    if not ws.exists() or memory_archive.archived(ws):
        return False
    try:
        rows = (
            db.query(MemoryItem)
            .filter(MemoryItem.user_id == user_id, MemoryItem.scope == "personal")
            .order_by(MemoryItem.id)
            .all()
        )
        memory_archive.write(
            ws,
            [
                {
                    "id": r.id,
                    "text": r.text,
                    "kind": r.kind,
                    "source": r.source,
                    "created": str(r.created_at or ""),
                }
                for r in rows
            ],
        )
        return True
    except Exception as e:  # noqa: BLE001 - an archive that cannot be written must never cost the person their answer
        log.warning("memory archive skipped: %s", e)
        return False
