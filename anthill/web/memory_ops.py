"""Memory promotion by corroboration.

When the same durable fact is independently held by two or more users, it stops being
"personal" - it is shared knowledge. ``maybe_corroborate`` detects that (a semantic
match across users) and promotes the whole matching group: to the **team** scope when
all holders share one team, otherwise to the **org** scope (recalled for everyone).
This is the read-side mirror of the wiki corroboration rung.
"""

from __future__ import annotations

from .. import memory as mem
from .db import MemoryItem, TeamMembership


def auto_memory_on(db, user_id) -> bool:
    """False when the user paused auto-memory (``User.auto_memory_off``); a missing user -> on
    (the default). Gates the auto-distillation of memories from chats / tasks / agent runs."""
    if not user_id:
        return True
    from .db import User

    u = db.query(User).filter(User.id == user_id).first()
    return not (u and getattr(u, "auto_memory_off", False))


def _similar(a, b, *, threshold: float) -> bool:
    """Are two memory items the same fact? Cosine when both carry embeddings, else a
    cheap normalised text match (so it still works with the embedder unavailable)."""
    va, vb = mem.decode_vec(a.embedding), mem.decode_vec(b.embedding)
    if va is not None and vb is not None:
        return mem.cosine(va, vb) >= threshold
    return not mem.is_new(a.text, [b.text])


def _common_team(db, user_ids) -> int | None:
    """A team every given user is an active member of, else None (lowest id if several)."""
    common = None
    for uid in user_ids:
        mine = {
            m.team_id
            for m in db.query(TeamMembership)
            .filter(TeamMembership.user_id == uid, TeamMembership.status == "active")
            .all()
        }
        common = mine if common is None else (common & mine)
        if not common:
            return None
    return min(common) if common else None


def _notify_promoted(db, user_ids, text: str, scope: str, n: int) -> None:
    """Best-effort push to each holder that their personal memory just became shared (team/org), so
    the promotion is visible - not silent - and they can act (keep it personal, or delete it)."""
    try:
        from .push import send_to_user

        where = "your team" if scope == "team" else "everyone in your org"
        for uid in user_ids:
            if uid:
                send_to_user(
                    db,
                    uid,
                    {
                        "title": "A memory is now shared",
                        "body": f'"{text[:80]}" is now recalled for {where} '
                        f"({n} of you have it). You can keep it personal or delete it in Memory.",
                        "url": "/memory",
                    },
                )
    except Exception:
        pass


def maybe_corroborate(
    db,
    org_id: int,
    item,
    *,
    threshold: float = 0.92,
    notification_queue: list[tuple[set[int], str, str, int]] | None = None,
):
    """If `item` (a freshly stored personal memory) is corroborated by >= 1 other user,
    promote the whole matching group. Returns the new scope ('team'|'org') or None.
    The caller commits.

    A memory the user marked "keep personal" (``pinned_personal``) is never promoted, and is never
    pulled into another item's promotion group. When a promotion does happen, every holder is
    notified so the (previously silent) personal -> shared move is visible + reversible."""
    if item is None or item.scope != "personal" or item.user_id is None or item.pinned_personal:
        return None
    others = (
        db.query(MemoryItem)
        .filter(
            MemoryItem.org_id == org_id,
            MemoryItem.scope == "personal",
            MemoryItem.user_id.isnot(None),
            MemoryItem.user_id != item.user_id,
            MemoryItem.pinned_personal.is_(False),  # a kept-personal memory never joins a promotion
        )
        .all()
    )
    matches = [o for o in others if _similar(item, o, threshold=threshold)]
    holders = {item.user_id} | {o.user_id for o in matches}
    if len(holders) < 2:
        return None
    team_id = _common_team(db, holders)
    new_scope = "team" if team_id else "org"
    for m in [*matches, item]:
        if team_id:
            m.scope, m.team_id = "team", team_id
        else:
            m.scope, m.team_id, m.user_id = "org", None, None
        m.corroborations = len(holders)
    notice = (holders, item.text, new_scope, len(holders))
    if notification_queue is None:
        _notify_promoted(db, *notice)
    else:
        notification_queue.append(notice)
    return new_scope
