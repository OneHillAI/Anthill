"""An item's own page: what it is and where it came from. Spec: docs/specs/item-page.md, issue #315. It only reads:
the item's record (anthill/wiki/items.py) and the Library's rows (anthill/web/library.py)."""

from __future__ import annotations

from ..wiki import items
from .answer_panel import SPACE_LABELS

ORIGIN_WORDS = {
    "own": "Uploaded or written by you",
    "model-written": "Written by a model",
    "person-edited": "Edited by a person",
    "imported": "Imported from another source",
}


def facts(rec: items.ItemRecord | None, space: str) -> dict:
    """What the record says about the item, in words. An item with no record has only its wiki."""
    out = {"wiki": SPACE_LABELS.get(space, space), "known": rec is not None}
    if rec is not None:
        out.update(
            origin=ORIGIN_WORDS.get(rec.origin, "Unknown"),
            version=rec.version,
            learned=rec.learned[:10],
            fingerprint=rec.content_hash[:12],
        )
    return out
