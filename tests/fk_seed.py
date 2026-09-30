"""Seed the parent rows that strict foreign-key enforcement (#634) now requires.

Many tests build their own rows with a hardcoded ``org_id=1`` (and ``user_id=1``/``2``) without ever
creating the parent ``Organization``/``User``. That only worked while foreign keys were unenforced. This
helper creates those canonical parents so a test's child rows have something to reference.
"""

from __future__ import annotations

from anthill.web.db import Organization, User


def seed_org_and_users(db, *, org_id: int = 1, user_ids=(1, 2)) -> None:
    """Idempotently create Organization(org_id) and the given Users, all bound to that org."""
    if db.get(Organization, org_id) is None:
        db.add(Organization(id=org_id, name="Test Org", slug=f"org{org_id}"))
        db.flush()
    for uid in user_ids:
        if db.get(User, uid) is None:
            db.add(
                User(
                    id=uid,
                    org_id=org_id,
                    email=f"u{uid}@test.local",
                    role="admin" if uid == 1 else "member",
                    active=True,
                )
            )
    db.flush()
