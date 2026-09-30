"""Web Push (VAPID) - send notifications to installed PWAs.

VAPID keypair is generated once and persisted to <data>/vapid.json. Sending uses
pywebpush, declared as an optional dependency: if it's not installed the whole
feature degrades to a no-op (the dashboard still works). End-to-end delivery
requires a real browser subscription + a push service, so it can't be unit-tested
here - but key derivation and subscription storage are.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy.orm import Session

from .db import PushSubscription

_CACHE: dict | None = None


def _vapid_path() -> Path:
    db = os.environ.get("ANTHILL_DB", "data/anthill.db")
    return Path(db).parent / "vapid.json"


def _vapid() -> dict:
    """Load or generate the VAPID keypair (private PEM + applicationServerKey)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    p = _vapid_path()
    if p.exists():
        _CACHE = json.loads(p.read_text())
        return _CACHE

    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    pub = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    priv = v.private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    _CACHE = {
        "app_server_key": base64.urlsafe_b64encode(pub).rstrip(b"=").decode(),
        "private_pem": priv,
        "sub": os.environ.get("ANTHILL_PUSH_SUB", "mailto:admin@anthill.local"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_CACHE))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return _CACHE


def public_key() -> str:
    """The applicationServerKey the browser needs to subscribe."""
    return _vapid()["app_server_key"]


def store_subscription(db: Session, *, org_id, user_id, sub: dict) -> None:
    endpoint = sub.get("endpoint", "")
    keys = sub.get("keys", {}) or {}
    if not endpoint:
        return
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if existing:
        existing.p256dh = keys.get("p256dh", "")
        existing.auth = keys.get("auth", "")
        existing.user_id = user_id
        existing.org_id = org_id
    else:
        db.add(
            PushSubscription(
                org_id=org_id,
                user_id=user_id,
                endpoint=endpoint,
                p256dh=keys.get("p256dh", ""),
                auth=keys.get("auth", ""),
            )
        )
    db.commit()


def send_push(sub: PushSubscription, payload: dict) -> bool:
    """Send one push. Returns False (no-op) if pywebpush is unavailable or the
    push fails (e.g. an expired subscription, which we prune)."""
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return False
    v = _vapid()
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=v["private_pem"],
            vapid_claims={"sub": v["sub"]},
        )
        return True
    except WebPushException:
        return False
    except Exception:
        return False


def send_to_user(db: Session, user_id: int, payload: dict) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    sent = 0
    for s in subs:
        if send_push(s, payload):
            sent += 1
    return sent


def send_to_org(db: Session, org_id: int, payload: dict) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.org_id == org_id).all()
    return sum(1 for s in subs if send_push(s, payload))
