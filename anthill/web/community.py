"""The invitation to the early-adopter Slack group that every new user gets once, in the notification bell.
Spec: docs/specs/early-adopter-slack-invite.md, issue #369. It reads the user's sign-up date and writes one
notification through the single ``notify()`` chokepoint; nothing is sent outside the app and the link is only shown."""

from __future__ import annotations

from datetime import datetime

from .db import Notification, User
from .notify import notify

KIND = "community"
INVITE_URL = "https://join.slack.com/t/onehill/shared_invite/zt-4eozypon9-tw3bvOaf_6MGwUChSgZCZw"
TITLE = "Join the early-adopter Slack group"
MESSAGE = (
    "Join the early-adopter Slack group to receive support, provide feedback, and steer the "
    "development of Anthill."
)
# People who signed up before this day are not invited: "every new user" means from the release on.
NEW_USERS_FROM = datetime(2026, 11, 25)


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


def ensure_invite(db, *, user_id: int, org_id: int | None) -> None:
    """Give a user who signed up on or after the release day the invitation, once. Safe to call on every page: it
    is one lookup when there is nothing to do, and it never raises into the page."""
    try:
        if db.query(Notification.id).filter_by(user_id=user_id, kind=KIND).first():
            return
        user = db.get(User, user_id)
        if user is None or user.created_at is None or _naive(user.created_at) < NEW_USERS_FROM:
            return
        notify(
            db,
            user_id=user_id,
            org_id=org_id,
            kind=KIND,
            title=TITLE,
            body=MESSAGE,
            link=INVITE_URL,
            push=False,
        )
        _keep_only_the_first(db, user_id)
    except Exception:  # noqa: BLE001 - an invitation must never cost the person their page
        db.rollback()


def _keep_only_the_first(db, user_id: int) -> None:
    """Two pages loading at the same moment can both find none and both create one: keep the first."""
    ids = [
        n.id
        for n in db.query(Notification.id)
        .filter_by(user_id=user_id, kind=KIND)
        .order_by(Notification.id)
        .all()
    ]
    if len(ids) > 1:
        db.query(Notification).filter(Notification.id.in_(ids[1:])).delete(
            synchronize_session=False
        )
        db.commit()


def open_invite(db, *, user_id: int, org_id: int | None) -> dict | None:
    """The invitation if this user has it and has not dealt with it (it is still unread): what the dashboard shows
    under "Needs your attention". Gives a new user the invitation first, so the first page already has it."""
    ensure_invite(db, user_id=user_id, org_id=org_id)
    try:
        n = (
            db.query(Notification)
            .filter_by(user_id=user_id, kind=KIND, read=False)
            .order_by(Notification.id)
            .first()
        )
    except Exception:  # noqa: BLE001 - the dashboard must render without it
        db.rollback()
        return None
    return {"title": n.title, "body": n.body, "url": n.link} if n else None


def mark_done(db, *, user_id: int) -> None:
    """The person has joined or does not want it now: mark it read, which also clears its bell count."""
    db.query(Notification).filter_by(user_id=user_id, kind=KIND, read=False).update(
        {Notification.read: True}
    )
    db.commit()
