"""The single in-app notification chokepoint (#284).

Every notification-worthy event calls ``notify()``. It persists one ``Notification`` (what the bell +
notification centre read) and, best-effort, sends a web push (the out-of-app channel for the same event).
Having one place to route from is the point: the app never grows another ad-hoc alert path, and adding a
new notification is one call.
"""

from __future__ import annotations

from .db import Notification


def notify(
    db,
    *,
    user_id: int,
    org_id: int | None = None,
    kind: str = "info",
    title: str,
    body: str = "",
    link: str = "",
    push: bool = True,
) -> Notification | None:
    """Record an in-app notification for one user and (best-effort) push it.

    A notification is a side effect, never the caller's main work, so this never raises into the caller: a
    DB error returns ``None`` and a push failure is swallowed. ``link`` is the in-app URL to act on it.
    """
    n = Notification(
        user_id=user_id,
        org_id=org_id,
        kind=(kind or "info")[:40],
        title=(title or "")[:200],
        body=(body or "")[:500],
        link=(link or "")[:300],
    )
    db.add(n)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return None
    if push:
        try:
            from . import push as push_mod

            push_mod.send_to_user(
                db,
                user_id,
                {
                    "title": n.title or "Anthill",
                    "body": n.body or n.title,
                    "url": n.link or "/notifications",
                },
            )
        except Exception:
            pass
    return n
