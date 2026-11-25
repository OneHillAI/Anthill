"""An item's own page: what it is and where it came from. Spec: docs/specs/item-page.md, issue #315. It only reads:
the item's record (anthill/wiki/items.py), the Library's rows (anthill/web/library.py) and, for where it was used,
the lookup in anthill/web/used_in.py (spec: docs/specs/item-used-in.md, issue #363)."""

from __future__ import annotations

from datetime import date, datetime

from ..wiki import items
from . import used_in
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


MAX_USED_ROWS = 20  # the answers listed, newest first; the rest are counted
ROUTE_WORDS = {"chat": "chat", "task": "task", "agent": "agent"}
STANDING_WORDS = {
    "unchanged": "as it was then",
    "changed": "has changed since",
    "gone": "no longer available to answers",
    "not_held": "its wiki is not on this machine",
}  # "unknown" (no hash was kept to compare) says nothing


def _link(route: str, ref: dict) -> str:
    """Where one answer opens: the chat, the task's result or the agent."""
    if route == "chat":
        return f"/chat/{int(ref['conversation'])}#bubble-{int(ref['message'])}"
    if route == "task":
        return f"/tasks/{int(ref['task'])}/result"
    return f"/agents/{int(ref['agent'])}"


def _day(at) -> str:
    return at.strftime("%Y-%m-%d") if isinstance(at, (date, datetime)) else ""


def used_rows(db, rec: items.ItemRecord | None, *, user_id: int, org_id: int | None) -> dict:
    """The answers the viewer may open whose record lists this item, for the "Used in" section:
    ``{tracked, rows, more}``. An item with no record has no id to look for, so its use is not tracked."""
    if rec is None or not rec.id:
        return {"tracked": False, "rows": [], "more": 0}
    found = used_in.used_in(db, rec.id, user_id=user_id, org_id=org_id)
    rows = []
    for r in found[:MAX_USED_ROWS]:
        try:
            href = _link(r["route"], r["ref"])
        except (KeyError, TypeError, ValueError):
            href = ""
        rows.append(
            {
                "title": str(r.get("title") or "Untitled"),
                "route": ROUTE_WORDS.get(str(r.get("route")), ""),
                "day": _day(r.get("at")),
                "version": int(r.get("version") or 0),
                "standing": STANDING_WORDS.get(str(r.get("status")), ""),
                "href": href,
            }
        )
    return {"tracked": True, "rows": rows, "more": max(len(found) - MAX_USED_ROWS, 0)}
