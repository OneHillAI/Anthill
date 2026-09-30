"""Shared memory recall: durable memories relevant to a query, as a bullet block.

Used by the chat path (plain + agent mode) and the scheduler so agent tasks also
benefit from what the system remembers about the user/org.
"""

from __future__ import annotations

from .db import MemoryItem


def recall_memory(db, org_id, user_id, question: str, k: int = 3, team_ids=None) -> str:
    from .. import memory as mem

    # The reader sees their own memory, their teams' memory, and org-wide memory.
    cond = (MemoryItem.user_id == user_id) | (MemoryItem.scope == "org")
    if team_ids:
        cond = cond | ((MemoryItem.scope == "team") & MemoryItem.team_id.in_(team_ids))
    rows = db.query(MemoryItem).filter(MemoryItem.org_id == org_id, cond).all()
    if not rows:
        return ""
    items = [(r.id, r.text, r.embedding) for r in rows]
    hits = mem.recall(items, question, mem.embed_text(question), k=k)
    return "\n".join(f"- {t}" for _id, t, _s in hits)
