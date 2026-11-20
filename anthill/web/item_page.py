"""An item's own page: what it is, where it came from, and which of the viewer's chats used it. Spec:
docs/specs/item-page.md, issue #315. It only reads: the item's record (anthill/wiki/items.py), the Library's rows
and the answer records kept with each answer (anthill/wiki/answer_record.py)."""

from __future__ import annotations

import json

from sqlalchemy import or_

from ..wiki import answer_record, items
from .answer_panel import SPACE_LABELS
from .db import ChatMessage, Conversation

ORIGIN_WORDS = {
    "own": "Uploaded or written by you",
    "model-written": "Written by a model",
    "person-edited": "Edited by a person",
    "imported": "Imported from another source",
}
MAX_CHATS = 20  # the chats listed, newest first; the rest are counted
MAX_ANSWERS_READ = (
    3000  # the newest answers of the viewer searched, so a huge history cannot slow the page
)


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


def _matches(
    entry: dict, *, record_id: str, kind: str, name: str, space: str, space_ref: str
) -> bool:
    """Whether an item of an answer record is this item: by its id, or, for an item with no record then, by what
    it is called and which wiki it is in."""
    if record_id and entry.get("id") == record_id:
        return True
    if entry.get("id"):
        return False
    return (
        entry.get("kind") == kind
        and (entry.get("slug") or entry.get("name")) == name
        and entry.get("space") == space
        and (entry.get("space_ref") or "") == space_ref
    )


def used_in(
    db,
    *,
    user_id: int,
    org_id: int,
    record_id: str,
    kind: str,
    name: str,
    space: str,
    space_ref: str,
) -> dict:
    """The viewer's own chats whose answers were given this item: ``{chats: [{id, title, answers}], more}``, newest
    chat first. Only the viewer's conversations are read."""
    # The newest answers of the viewer, narrowed by the database to those whose record mentions the item (its id
    # or its name, the name also as JSON writes it), so most records are never read into Python.
    newest = (
        db.query(ChatMessage.id)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .filter(
            Conversation.user_id == user_id,
            Conversation.org_id == org_id,
            ChatMessage.role == "assistant",
            ChatMessage.manifest != "",
        )
        .order_by(ChatMessage.id.desc())
        .limit(MAX_ANSWERS_READ)
        .subquery()
    )
    needles = [n for n in (record_id, name, json.dumps(name)[1:-1]) if n]
    rows = (
        db.query(ChatMessage.conversation_id, ChatMessage.manifest, Conversation.title)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .filter(
            ChatMessage.id.in_(db.query(newest.c.id)),
            or_(*[ChatMessage.manifest.contains(n, autoescape=True) for n in needles]),
        )
        .order_by(ChatMessage.id.desc())
        .all()
    )
    chats: dict[int, dict] = {}
    for conv_id, manifest, title in rows:
        entries = answer_record.loads(manifest).get("items", [])
        if any(
            isinstance(e, dict)
            and _matches(
                e, record_id=record_id, kind=kind, name=name, space=space, space_ref=space_ref
            )
            for e in entries
        ):
            chat = chats.setdefault(
                conv_id, {"id": conv_id, "title": title or "Untitled chat", "answers": 0}
            )
            chat["answers"] += 1
    ordered = list(chats.values())  # newest answer first, so the newest chat first
    return {"chats": ordered[:MAX_CHATS], "more": max(len(ordered) - MAX_CHATS, 0)}
