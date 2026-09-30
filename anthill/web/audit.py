from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .db import AuditLog

if TYPE_CHECKING:
    from .plane_routing import PlaneInference

# Pre-authentication security events. These happen before a session exists, so the acting user's org
# often can't be resolved (an unknown email, a throttled or denied IP) and the row is stored with
# org_id=NULL. They are also the only events ever written with a NULL org, and the exact signals an
# admin watches during an attack - so the admin Audit view and the anomaly detector deliberately
# surface them even when unattributed (issue #451). Attributable cases (a wrong password for a real
# account) still get org_id set at the log site, so real per-org attacks stay scoped to their org.
PREAUTH_EVENTS = frozenset(
    {"user.login_fail", "user.reset_noop", "user.login_throttled", "user.login_denied"}
)


def _org_or_preauth(org_id: int):
    """Filter predicate: rows owned by `org_id`, plus unattributed pre-auth security events."""
    return or_(
        AuditLog.org_id == org_id,
        and_(AuditLog.org_id.is_(None), AuditLog.event.in_(PREAUTH_EVENTS)),
    )


def log(
    db: Session,
    event: str,
    detail: str = "",
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    ip: str | None = None,
    registry_id: int | None = None,
) -> None:
    """Write one audit row. ``registry_id`` (#683 phase 7) links this row to the KnowledgeItem it
    acted on, when the caller already has that row in hand (e.g. right after an approve/create call
    resolved it) - pass it only where it's cheaply available; leave it unset (NULL) everywhere else,
    including deletes (the registry row is already gone by then)."""
    db.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            event=event,
            detail=detail,
            ip=ip,
            registry_id=registry_id,
        )
    )
    db.commit()


def log_inference_call(
    db: Session,
    plane_inf: PlaneInference,
    *,
    org_id: int | None,
    user_id: int | None,
    surface: str,
) -> None:
    """Record which model/provider answered one turn - the perimeter-crossing trail an admin needs
    (who, when, which model, which endpoint). Never the message content itself - that's what the
    egress scrubber in OpenAICompatBackend is for. Must never break the run it's called from."""
    try:
        log(
            db,
            "inference.call",
            f"surface={surface} plane={plane_inf.plane} backend={plane_inf.backend} "
            f"model={plane_inf.model} endpoint={plane_inf.base_url}",
            org_id=org_id,
            user_id=user_id,
        )
    except Exception:
        pass


# ── anomaly detection ─────────────────────────────────────────────────────────
# Simple threshold rules; no ML needed for alpha.

RULES = [
    # (event_prefix, window_minutes, max_count, alert_message)
    ("user.login_fail", 10, 5, "Brute-force attempt: >5 failed logins in 10 min"),
    ("user.login", 60, 50, "Unusual login volume: >50 logins in 1 hour"),
    ("wiki.promote", 5, 10, "Unusual promotion rate: >10 promotions in 5 min"),
    ("api.query", 1, 30, "Query spike: >30 queries in 1 min"),
]


# Login brute-force throttle: block an IP after too many failed sign-ins in the window. Keyed on IP
# (not email) so an attacker can't lock a victim out of their own account by guessing their address.
_LOGIN_FAIL_WINDOW_MIN = 15
_LOGIN_FAIL_MAX = 10


def too_many_login_fails(db: Session, ip: str | None) -> bool:
    """True once this IP has hit _LOGIN_FAIL_MAX failed logins within the window - the caller then
    rejects the attempt without checking the password. No IP (empty) is never throttled."""
    if not ip:
        return False
    since = datetime.now(timezone.utc) - timedelta(minutes=_LOGIN_FAIL_WINDOW_MIN)
    n = (
        db.query(AuditLog)
        .filter(
            AuditLog.event == "user.login_fail",
            AuditLog.ip == ip,
            AuditLog.created_at >= since,
        )
        .count()
    )
    return n >= _LOGIN_FAIL_MAX


def check_anomalies(db: Session, org_id: int) -> list[str]:
    """Return a list of alert strings for any triggered rules in the last hour."""
    alerts = []
    now = datetime.now(timezone.utc)
    for prefix, window_min, max_count, message in RULES:
        since = now - timedelta(minutes=window_min)
        count = (
            db.query(AuditLog)
            .filter(
                _org_or_preauth(org_id),
                AuditLog.event.like(f"{prefix}%"),
                AuditLog.created_at >= since,
            )
            .count()
        )
        if count > max_count:
            alerts.append(f"{message} (saw {count})")
    return alerts


def recent_events(db: Session, org_id: int, limit: int = 50) -> list[AuditLog]:
    """The org's events, plus unattributed pre-auth security events (failed logins, reset probes,
    throttles, denied SSO) stored with org_id=NULL. Without them an admin's Audit log would never
    show sign-in failures against the install - the exact signal to watch during an attack (#451)."""
    return (
        db.query(AuditLog)
        .filter(_org_or_preauth(org_id))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
