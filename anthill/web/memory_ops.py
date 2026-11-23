"""Memory switches. A memory stays where its owner made it; sharing one is the owner's own action."""

from __future__ import annotations


def auto_memory_on(db, user_id) -> bool:
    """False when the user paused auto-memory (``User.auto_memory_off``); a missing user -> on
    (the default). Gates the auto-distillation of memories from chats / tasks / agent runs."""
    if not user_id:
        return True
    from .db import User

    u = db.query(User).filter(User.id == user_id).first()
    return not (u and getattr(u, "auto_memory_off", False))
