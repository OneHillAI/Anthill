from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt_lib
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import keystore

# ── password hashing (bcrypt, cost 12) ───────────────────────────────────────


def hash_password(plain: str) -> str:
    return _bcrypt_lib.hashpw(plain.encode(), _bcrypt_lib.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt_lib.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── AES-256-GCM at rest ───────────────────────────────────────────────────────
# The key comes from anthill.keystore on first use (never made up here: a missing key refuses).
# ``_KEY`` and ``_JWT_SECRET`` stay as overrides (tests set them, bring-in assigns them).
# The ciphertext format is unchanged: base64(nonce + ciphertext + tag).

_KEY: bytes | None = None
_JWT_SECRET: str | None = None


def _data_key() -> bytes:
    return _KEY if _KEY is not None else keystore.data_key()


def _jwt_secret() -> str:
    return _JWT_SECRET if _JWT_SECRET is not None else keystore.jwt_secret()


def encrypt(plaintext: str) -> str:
    """AES-256-GCM encrypt. Returns base64(nonce + ciphertext + tag)."""
    nonce = secrets.token_bytes(12)  # 96-bit nonce, GCM standard
    ct = AESGCM(_data_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_with_key(key: bytes, token: str) -> str:
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def decrypt(token: str) -> str:
    return decrypt_with_key(_data_key(), token)


# ── JWT session tokens ────────────────────────────────────────────────────────

_ALGORITHM = "HS256"
_CLOCK_LEEWAY_SECONDS = (
    10  # PyJWT rejects an iat in the future; python-jose did not, so allow a little clock drift
)
_TTL_HOURS = int(os.environ.get("ANTHILL_SESSION_HOURS", str(30 * 24)))
SESSION_MAX_AGE_SECONDS = _TTL_HOURS * 60 * 60
_SESSION_RENEW_AFTER_SECONDS = min(60 * 60, max(1, SESSION_MAX_AGE_SECONDS // 2))


def make_token(
    user_id: int,
    org_id: int,
    role: str,
    *,
    session_id: str | None = None,
    auth_version: int = 0,
    session_order: int = 0,
    device_id: str = "",
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "org": org_id,
        "role": role,
        "sid": session_id or secrets.token_urlsafe(24),
        "ver": auth_version,
        "ord": session_order,
        "dev": device_id,
        "iat": now,
        "exp": now + timedelta(hours=_TTL_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def session_needs_renewal(claims: dict) -> bool:
    """Renew legacy tokens immediately and tokens that reach the configured age threshold."""
    try:
        issued_at = float(claims["iat"])
    except (KeyError, TypeError, ValueError):
        return True
    return datetime.now(timezone.utc).timestamp() - issued_at >= _SESSION_RENEW_AFTER_SECONDS


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError (imported as JWTError in app.py) on invalid/expired tokens."""
    return jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM], leeway=_CLOCK_LEEWAY_SECONDS)


# ── invite tokens ─────────────────────────────────────────────────────────────


def make_invite_token() -> str:
    return secrets.token_urlsafe(32)
