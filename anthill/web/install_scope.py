"""Install-wide scope helpers (docs/specs/invite-only-signup.md).

Some things belong to the whole install rather than to one organisation: who may create accounts on a server
other people can reach, and the device-level controls. These helpers answer the questions the routes ask:

- ``server_is_shared`` - can people other than the person at the machine reach this server?
- ``install_owner`` / ``is_install_owner`` - who administers the install? An explicit record, not a guess.
- ``request_is_local`` - did this request come from the machine itself, not through a network or a tunnel?
- ``install_scope_allowed`` - may this user change something that belongs to the whole install?
- ``signup_is_open`` - may a new account be created at ``/setup``?

A server counts as local only when the desktop app says so (``ANTHILL_LOCAL_ONLY=1``, set by the desktop
entry point, which owns the machine it runs on) and it listens on loopback. Every other way of starting it
(``anthill web``, the container, ``uvicorn`` by hand) counts as shared, because a loopback bind says nothing
about who reaches it: a reverse proxy, ``ssh -R``, ``socat`` or ``ngrok tcp`` on the same machine deliver
remote requests to it. An operator who runs ``anthill web`` for themselves on their own machine opts in by
setting ``ANTHILL_LOCAL_ONLY=1`` and must not do so behind a proxy or tunnel.

Remaining gap, stated plainly: a server marked local trusts a request that looks like the machine's own
(loopback client, loopback ``Host``, no forwarding header). That includes the desktop app, ``start.sh``,
``make alpha``, the macOS auto-start agent and ``scripts/start.ps1``, which set the mark. A proxy that adds
no forwarding header (a default nginx ``proxy_pass``, HAProxy without ``forwardfor``, a TLS-terminating TCP
proxy) or a TCP forward (``ssh -R``, ``socat``, ``ngrok tcp``) in front of one of them delivers remote
requests that look exactly like that. The README and ``docs/setup.md`` say not to do it unless Remote access
is switched on first (which makes the server shared) or ``ANTHILL_LOCAL_ONLY=0`` is set. The launchers honour
an ``ANTHILL_LOCAL_ONLY`` already in the environment. Every other way of starting the server is shared, and
a shared server trusts only the install owner.
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from .db import InstallSettings, OrgSettings, User

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "[::1]"}
# Headers a reverse proxy or tunnel adds. A request that carries one did not come from the machine itself,
# even when the proxy runs on it. A forwarder that adds none (a plain TCP forward) is not caught here, which
# is why a shared server accepts only the install owner and never relies on this check.
_FORWARDING_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
    "x-client-ip",
    "true-client-ip",
    "cf-connecting-ip",
    "via",
)


def _hostname(value: str) -> str:
    """Host part of a ``Host`` header value or bind address, lower-cased, without a port."""
    v = (value or "").strip().lower()
    if v.startswith("["):  # [::1]:8000
        return v.split("]")[0] + "]"
    return v.split(":")[0] if v.count(":") <= 1 else v


def bind_is_loopback() -> bool:
    """True only when the server is known to listen on the loopback address alone. The entry points record
    where they listen in ``ANTHILL_HOST`` just before uvicorn starts; missing or unknown is False."""
    host = os.environ.get("ANTHILL_HOST", "")
    return bool(host) and _hostname(host) in _LOOPBACK


def local_only() -> bool:
    """True only for a server started as someone's own machine: it listens on loopback and the desktop app
    (or an operator who opted in) set ``ANTHILL_LOCAL_ONLY=1``. Everything else is shared."""
    return bind_is_loopback() and os.environ.get("ANTHILL_LOCAL_ONLY", "").strip() == "1"


def remote_access_is_on(db: Session) -> bool:
    """True when any organisation has switched Remote access on (a tunnel or a public URL)."""
    return (
        db.query(OrgSettings.id)
        .filter(OrgSettings.remote_access_provider.notin_(["off", ""]))
        .first()
        is not None
    )


def server_is_shared(db: Session) -> bool:
    """True when people other than the person at the machine can reach this server: it is not a desktop (or
    an operator's opted-in local run), or Remote access is on."""
    return not local_only() or remote_access_is_on(db)


# ── the install owner: an explicit record ───────────────────────────────────────


INSTALL_ROW_ID = 1  # the install settings are one row with a fixed primary key


def _settings(db: Session) -> InstallSettings | None:
    return db.get(InstallSettings, INSTALL_ROW_ID)


def settings_row(db: Session) -> InstallSettings:
    """The install settings row, created on first use."""
    row = _settings(db)
    if row is None:
        row = InstallSettings(id=INSTALL_ROW_ID)
        db.add(row)
        db.flush()
    return row


def recorded_owner_id(db: Session) -> int | None:
    """The user id recorded as install owner, whether or not that account is active right now."""
    row = _settings(db)
    return int(row.owner_user_id) if row is not None and row.owner_user_id else None


def install_owner(db: Session) -> User | None:
    """The account recorded as the install owner, only while it is an active admin. None otherwise: nothing
    install-wide can then be changed by anyone, and sign-up on a shared server stays closed."""
    owner_id = recorded_owner_id(db)
    if owner_id is None:
        return None
    owner = db.query(User).filter(User.id == owner_id).first()
    if owner is None or not owner.active or owner.role != "admin":
        return None
    return owner


def install_owner_id(db: Session) -> int | None:
    owner = install_owner(db)
    return int(owner.id) if owner else None


def owner_org_id(db: Session) -> int | None:
    """The organisation of the recorded install owner, whether or not that account is active right now.
    None when no owner is recorded."""
    owner_id = recorded_owner_id(db)
    if owner_id is None:
        return None
    row = db.query(User).filter(User.id == owner_id).first()
    return int(row.org_id) if row is not None else None


def record_first_install_owner(db: Session, user: User) -> bool:
    """The very first account on an install is its owner. Call it in the same transaction as the insert of the
    user (after the flush, before the commit), so there is no moment with a first account and no owner. A later
    account never takes it. Returns True when it recorded the owner."""
    if db.query(User.id).count() != 1:
        return False
    row = settings_row(db)
    if row.owner_user_id:
        return False
    row.owner_user_id = int(user.id)  # type: ignore[assignment]  # SQLAlchemy Column
    return True


def backfill_install_owner(db: Session) -> int | None:
    """For an install that predates the record, or whose recorded owner never became an active admin (an
    account still waiting for its verification link): the first active admin becomes the owner. Returns the
    new owner's user id, or None when nothing changed. The caller commits."""
    current = recorded_owner_id(db)
    if current is not None and install_owner(db) is not None:
        return None
    first = (
        db.query(User).filter(User.role == "admin", User.active.is_(True)).order_by(User.id).first()
    )
    if first is None or first.id == current:
        return None
    settings_row(db).owner_user_id = first.id  # type: ignore[assignment]  # SQLAlchemy Column
    return int(first.id)


def set_install_owner_by_email(db: Session, email: str) -> User | None:
    """Make the active admin with this email the owner (the host-side recovery path: ``anthill owner set`` and
    ``ANTHILL_INSTALL_OWNER``). Returns that user, or None when there is no such active admin. The caller
    commits."""
    target = (
        db.query(User)
        .filter(
            User.email == (email or "").strip().lower(), User.role == "admin", User.active.is_(True)
        )
        .first()
    )
    if target is None:
        return None
    settings_row(db).owner_user_id = target.id  # type: ignore[assignment]  # SQLAlchemy Column
    return target


def transfer_install_owner(db: Session, new_owner_id: int) -> bool:
    """Hand the install to another active admin of the owner's own organisation. The caller has checked that
    the current owner asked."""
    current = install_owner(db)
    target = db.query(User).filter(User.id == int(new_owner_id)).first()
    if (
        current is None
        or target is None
        or not target.active
        or target.role != "admin"
        or target.org_id != current.org_id
    ):
        return False
    settings_row(db).owner_user_id = target.id  # type: ignore[assignment]  # SQLAlchemy Column
    return True


def is_install_owner(db: Session, user: dict) -> bool:
    """True when the signed-in user is the recorded install owner (and still an active admin)."""
    try:
        uid = int(str(user.get("sub")))
    except ValueError:
        return False
    return user.get("role") == "admin" and uid == install_owner_id(db)


# ── is this request from the machine itself? ────────────────────────────────────


def request_is_local(request) -> bool:
    """True when the request came from this machine itself: a loopback client, addressed to a loopback host
    name, with no proxy or tunnel header. A tunnel to a local port is not local, because its requests are
    addressed to the public name and carry forwarding headers."""
    client = getattr(request, "client", None)
    if client is None or _hostname(client.host) not in _LOOPBACK:
        return False
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK:
        return False
    return not any(h in request.headers for h in _FORWARDING_HEADERS)


def install_scope_allowed(db: Session, request, user: dict) -> bool:
    """May this user change something that belongs to the whole install (device profiles, built-in skills,
    Remote access)? The install owner, always. Anyone else only on a server that is not shared and only from
    the machine itself: on a shared server a same-machine forwarder can make a remote request look local, so
    only the owner is trusted there."""
    if is_install_owner(db, user):
        return True
    return not server_is_shared(db) and request_is_local(request)


# ── sign-up ─────────────────────────────────────────────────────────────────────


def signup_is_open(db: Session, request=None) -> bool:
    """Whether a new account may be created at ``/setup``.

    - The very first account is always open.
    - On a shared server, later accounts are by invitation unless the install owner switched sign-up on. With
      users but no active owner it fails closed (stays by invitation).
    - On a server that is not shared, later accounts may sign up from the machine itself only; a request that
      arrives through a forwarder or tunnel is refused.
    """
    if db.query(User.id).first() is None:
        return True
    if server_is_shared(db):
        if install_owner(db) is None:
            return False
        row = _settings(db)
        return bool(row and row.signup_open)
    return request is not None and request_is_local(request)
