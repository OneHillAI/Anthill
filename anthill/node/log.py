"""Signed, numbered, hash-chained JSON record log and Hill membership (Sec 4.1, 4.2, Rules 1, 5).

Every record carries the locked format fields:
  version, alg, payload_type, scope, record_type, hill_id, author_root_key, device_key,
  seq, prev_hash, timestamp, model, payload, content_ref, content_hash, record_hash, signature.

Page content is stored as a detached blob referenced by ``content_ref`` + ``content_hash`` so
bodies can be erased by a ``Deletion`` record without breaking hash-chain verification.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import NODE_PROTOCOL_VERSION, PAYLOAD_TYPES, RECORD_ALG, RECORD_TYPES, SCOPES
from .identity import ed25519_sign, ed25519_verify

GENESIS_PREV_HASH = "genesis"

_REQUIRED_FIELDS = frozenset(
    {
        "version",
        "alg",
        "payload_type",
        "scope",
        "record_type",
        "hill_id",
        "author_root_key",
        "device_key",
        "seq",
        "prev_hash",
        "timestamp",
        "model",
        "payload",
        "content_ref",
        "content_hash",
        "record_hash",
        "signature",
    }
)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def make_content_ref(body: str) -> tuple[str, str]:
    """Return ``(content_ref, content_hash)`` for a detached page body."""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"blob://{digest}", f"sha256:{digest}"


def verify_content_blob(body: str, content_hash: str) -> bool:
    return sha256_prefixed(body.encode("utf-8")) == content_hash


def compute_record_hash(unsigned_envelope: dict[str, Any]) -> str:
    body = {k: v for k, v in unsigned_envelope.items() if k not in ("record_hash", "signature")}
    return sha256_prefixed(canonical_json(body).encode("utf-8"))


def create_record(
    *,
    record_type: str,
    scope: str,
    hill_id: str,
    author_root_key: str,
    device_key: str,
    device_priv_b64: str,
    seq: int,
    prev_hash: str,
    payload: dict[str, Any],
    payload_type: str = "plain",
    model: str = "",
    content_body: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build and sign a locked-format chained record."""
    if record_type not in RECORD_TYPES:
        raise ValueError(f"Unsupported record_type: {record_type}")
    if scope not in SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")
    if payload_type not in PAYLOAD_TYPES:
        raise ValueError(f"Unsupported payload_type: {payload_type}")
    if seq < 1:
        raise ValueError("Record seq must be >= 1.")
    if seq == 1 and prev_hash != GENESIS_PREV_HASH:
        raise ValueError("First record in a device chain must use prev_hash='genesis'.")

    content_ref: str | None = None
    content_hash: str | None = None
    if content_body is not None:
        content_ref, content_hash = make_content_ref(content_body)

    envelope: dict[str, Any] = {
        "version": NODE_PROTOCOL_VERSION,
        "alg": RECORD_ALG,
        "payload_type": payload_type,
        "scope": scope,
        "record_type": record_type,
        "hill_id": hill_id,
        "author_root_key": author_root_key,
        "device_key": device_key,
        "seq": seq,
        "prev_hash": prev_hash,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "model": model,
        "payload": payload,
        "content_ref": content_ref,
        "content_hash": content_hash,
    }
    record_hash = compute_record_hash(envelope)
    envelope["record_hash"] = record_hash
    envelope["signature"] = ed25519_sign(device_priv_b64, record_hash.encode("utf-8"))
    return envelope


def verify_record(
    record: dict[str, Any],
    *,
    expected_prev_hash: str | None = None,
    expected_seq: int | None = None,
    active_members: set[str] | None = None,
    revoked_devices: set[str] | None = None,
    device_bindings: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Verify signature, hash integrity, chain continuity, device binding, and membership."""
    if not isinstance(record, dict):
        return False, "record is not a JSON object"
    missing = _REQUIRED_FIELDS - set(record.keys())
    if missing:
        return False, f"missing required fields: {sorted(missing)}"
    if record["version"] != NODE_PROTOCOL_VERSION:
        return False, f"unsupported version: {record['version']}"
    if record["alg"] != RECORD_ALG:
        return False, f"unsupported alg: {record['alg']}"
    if record["payload_type"] not in PAYLOAD_TYPES:
        return False, f"invalid payload_type: {record['payload_type']}"
    if record["scope"] not in SCOPES:
        return False, f"invalid scope: {record['scope']}"
    if record["record_type"] not in RECORD_TYPES:
        return False, f"invalid record_type: {record['record_type']}"
    if not isinstance(record["seq"], int) or record["seq"] < 1:
        return False, "seq must be a positive integer"
    if not isinstance(record["payload"], dict):
        return False, "payload must be an object"

    sig = str(record.get("signature") or "")
    if not sig:
        return False, "unsigned record refused (Rule 1 & 5)"

    author_root = str(record["author_root_key"])
    device_key = str(record["device_key"])
    if not author_root or not device_key:
        return False, "missing author_root_key or device_key"

    if revoked_devices and device_key in revoked_devices:
        return False, "device key is revoked (Rule 7)"

    if active_members is not None and author_root not in active_members:
        return False, "author is not in the Hill active membership list"

    if device_bindings is not None and device_key != author_root:
        bound_root = device_bindings.get(device_key)
        if bound_root != author_root:
            return False, "device_key is not bound to author_root_key"

    recomputed = compute_record_hash(record)
    if record["record_hash"] != recomputed:
        return False, "record_hash mismatch (tampered record)"

    if not ed25519_verify(device_key, recomputed.encode("utf-8"), sig):
        return False, "invalid Ed25519 signature"

    if expected_seq is not None and record["seq"] != expected_seq:
        return False, f"seq mismatch: expected {expected_seq}, got {record['seq']}"

    if expected_prev_hash is not None and record["prev_hash"] != expected_prev_hash:
        return False, "prev_hash mismatch (broken chain)"

    return True, "ok"


def verify_chain(
    records: list[dict[str, Any]],
    *,
    active_members: set[str] | None = None,
    revoked_devices: set[str] | None = None,
    device_bindings: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Verify per-device chains across a list of records."""
    by_device: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_device.setdefault(str(rec.get("device_key", "")), []).append(rec)

    for chain in by_device.values():
        ordered = sorted(chain, key=lambda r: int(r.get("seq", 0)))
        prev_hash = GENESIS_PREV_HASH
        expected_seq = 1
        for rec in ordered:
            ok, reason = verify_record(
                rec,
                expected_prev_hash=prev_hash,
                expected_seq=expected_seq,
                active_members=active_members,
                revoked_devices=revoked_devices,
                device_bindings=device_bindings,
            )
            if not ok:
                return False, f"seq {rec.get('seq')}: {reason}"
            prev_hash = rec["record_hash"]
            expected_seq += 1
    return True, "ok"


def resolve_membership(
    records: list[dict[str, Any]],
    admin_root_key: str,
    *,
    device_bindings: dict[str, str] | None = None,
) -> tuple[set[str], set[str], int]:
    """Derive active member root keys and revoked device keys from verified admin Membership records."""
    members: set[str] = {admin_root_key}
    revoked: set[str] = set()
    latest_seq = 0

    admin_membership = [
        r
        for r in records
        if r.get("record_type") == "Membership" and r.get("author_root_key") == admin_root_key
    ]
    admin_membership.sort(key=lambda r: int(r.get("seq", 0)))

    for rec in admin_membership:
        ok, _ = verify_record(rec, device_bindings=device_bindings)
        if not ok:
            continue
        payload = rec["payload"]
        raw_members = payload.get("members")
        if isinstance(raw_members, list):
            members = {str(m) for m in raw_members if isinstance(m, str) and m}
            members.add(admin_root_key)
        raw_revoked = payload.get("revoked_devices")
        if isinstance(raw_revoked, list):
            revoked.update(str(d) for d in raw_revoked if isinstance(d, str) and d)
        latest_seq = max(latest_seq, int(rec["seq"]))

    return members, revoked, latest_seq
