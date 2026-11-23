"""Desktop-to-desktop pairing with expiring one-time tokens and SAS verification (Sec 5).

Tokens carry the inviter's pinned public keys, Hill metadata, optional direct endpoint hint,
and an expiring single-use nonce. Both desktops compute the identical 6-digit Short
Authentication String (SAS) from both Ed25519 root keys + nonce; keys are trusted only after
human confirmation that both screens show the same code.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from . import SCOPES
from .identity import (
    b64u_decode,
    b64u_encode,
    ed25519_sign,
    ed25519_verify,
    verify_device_binding,
)
from .log import canonical_json

PAIR_PREFIX = "anthill-pair://v1/"
DEFAULT_INVITE_TTL_MINUTES = 10


class PairingError(ValueError):
    """Invalid, expired, or tampered pairing token or handshake."""


def compute_sas_code(root_key_a: str, root_key_b: str, nonce: str) -> str:
    """Compute a deterministic 6-digit SAS code (e.g. ``482 - 917``) from both root keys + nonce."""
    low, high = sorted((root_key_a, root_key_b))
    digest = hashlib.sha256(f"anthill-pair-sas-v1:{low}:{high}:{nonce}".encode()).digest()
    num = int.from_bytes(digest[:4], "big") % 1_000_000
    return f"{num // 1000:03d} - {num % 1000:03d}"


def create_invite_token(
    *,
    hill_id: str,
    hill_name: str,
    scope: str,
    admin_root_key: str,
    inviter_name: str,
    inviter_root_key: str,
    inviter_root_priv_b64: str,
    inviter_device_key: str,
    inviter_device_name: str,
    inviter_x25519_key: str,
    inviter_binding_sig: str,
    inviter_model: str = "local",
    endpoint_url: str = "",
    ttl_minutes: int = DEFAULT_INVITE_TTL_MINUTES,
) -> tuple[str, dict[str, Any]]:
    """Create a signed one-time pairing token ``anthill-pair://v1/...``."""
    if scope not in SCOPES:
        raise PairingError(f"Invalid Hill scope: {scope}")
    nonce = secrets.token_hex(12)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    body: dict[str, Any] = {
        "v": 1,
        "hill_id": hill_id,
        "hill_name": hill_name,
        "scope": scope,
        "admin_root_key": admin_root_key,
        "inviter_name": inviter_name,
        "inviter_root_key": inviter_root_key,
        "inviter_device_key": inviter_device_key,
        "inviter_device_name": inviter_device_name,
        "inviter_x25519_key": inviter_x25519_key,
        "inviter_binding_sig": inviter_binding_sig,
        "inviter_model": inviter_model,
        "endpoint_url": endpoint_url.strip().rstrip("/"),
        "nonce": nonce,
        "expires_at": expires_at,
    }
    sig = ed25519_sign(inviter_root_priv_b64, canonical_json(body).encode("utf-8"))
    envelope = {**body, "sig": sig}
    token = PAIR_PREFIX + b64u_encode(canonical_json(envelope).encode("utf-8"))
    return token, envelope


def parse_invite_token(token: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Decode and cryptographically verify an expiring pairing token."""
    raw_token = (token or "").strip()
    if not raw_token.startswith(PAIR_PREFIX):
        raise PairingError("Pairing token must start with anthill-pair://v1/.")
    encoded = raw_token[len(PAIR_PREFIX) :]
    try:
        envelope = json.loads(b64u_decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise PairingError("Malformed pairing token payload.") from exc
    if not isinstance(envelope, dict) or envelope.get("v") != 1:
        raise PairingError("Unsupported pairing token version.")

    sig = str(envelope.get("sig") or "")
    body = {k: v for k, v in envelope.items() if k != "sig"}
    required = (
        "hill_id",
        "hill_name",
        "scope",
        "admin_root_key",
        "inviter_root_key",
        "inviter_device_key",
        "inviter_device_name",
        "inviter_x25519_key",
        "inviter_binding_sig",
        "nonce",
        "expires_at",
    )
    if not all(body.get(k) for k in required):
        raise PairingError("Pairing token is missing required fields.")

    if not ed25519_verify(str(body["inviter_root_key"]), canonical_json(body).encode("utf-8"), sig):
        raise PairingError("Pairing token signature is invalid.")

    if not verify_device_binding(
        str(body["inviter_root_key"]),
        str(body["inviter_device_key"]),
        str(body["inviter_x25519_key"]),
        str(body["inviter_device_name"]),
        str(body["inviter_binding_sig"]),
    ):
        raise PairingError("Inviter device key binding signature is invalid.")

    try:
        exp = datetime.fromisoformat(str(body["expires_at"]))
    except ValueError as exc:
        raise PairingError("Pairing token expiry timestamp is invalid.") from exc
    ref_now = now or datetime.now(timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if ref_now > exp:
        raise PairingError("Pairing token has expired.")

    return envelope


def create_join_response(
    *,
    nonce: str,
    hill_id: str,
    joiner_name: str,
    joiner_root_key: str,
    joiner_root_priv_b64: str,
    joiner_device_key: str,
    joiner_device_name: str,
    joiner_x25519_key: str,
    joiner_binding_sig: str,
    joiner_model: str = "local",
    endpoint_url: str = "",
) -> dict[str, Any]:
    """Create a signed joiner handshake payload (sent over HTTP or pasted offline)."""
    body: dict[str, Any] = {
        "v": 1,
        "nonce": nonce,
        "hill_id": hill_id,
        "joiner_name": joiner_name,
        "joiner_root_key": joiner_root_key,
        "joiner_device_key": joiner_device_key,
        "joiner_device_name": joiner_device_name,
        "joiner_x25519_key": joiner_x25519_key,
        "joiner_binding_sig": joiner_binding_sig,
        "joiner_model": joiner_model,
        "endpoint_url": endpoint_url.strip().rstrip("/"),
    }
    sig = ed25519_sign(joiner_root_priv_b64, canonical_json(body).encode("utf-8"))
    return {**body, "sig": sig}


def verify_join_response(resp: dict[str, Any], *, expected_nonce: str) -> dict[str, Any]:
    """Verify a joiner's signed pairing handshake payload."""
    if not isinstance(resp, dict) or resp.get("v") != 1:
        raise PairingError("Invalid join response.")
    if resp.get("nonce") != expected_nonce:
        raise PairingError("Join response nonce does not match this invite.")
    sig = str(resp.get("sig") or "")
    body = {k: v for k, v in resp.items() if k != "sig"}
    required = (
        "nonce",
        "hill_id",
        "joiner_name",
        "joiner_root_key",
        "joiner_device_key",
        "joiner_device_name",
        "joiner_x25519_key",
        "joiner_binding_sig",
    )
    if not all(body.get(k) for k in required):
        raise PairingError("Join response is missing required fields.")
    if not ed25519_verify(str(body["joiner_root_key"]), canonical_json(body).encode("utf-8"), sig):
        raise PairingError("Join response signature is invalid.")
    if not verify_device_binding(
        str(body["joiner_root_key"]),
        str(body["joiner_device_key"]),
        str(body["joiner_x25519_key"]),
        str(body["joiner_device_name"]),
        str(body["joiner_binding_sig"]),
    ):
        raise PairingError("Joiner device binding signature is invalid.")
    return resp
