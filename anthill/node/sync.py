"""Transport-agnostic delta sync protocol (Sec 4.1 #4).

Compares per-device latest ``seq`` heads and exchanges only missing records. Every incoming
record is verified against Ed25519 signatures, chain links, detached content hashes, device
revocations, and the Hill's signed admin ``Membership`` list before acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .identity import verify_device_binding
from .log import GENESIS_PREV_HASH, verify_content_blob, verify_record


@dataclass
class SyncBatchResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    page_changes: list[dict[str, Any]] = field(default_factory=list)
    membership_updates: list[dict[str, Any]] = field(default_factory=list)
    active_members: set[str] = field(default_factory=set)
    revoked_devices: set[str] = field(default_factory=set)


def compute_heads(records: list[dict[str, Any]]) -> dict[str, int]:
    """Return ``{device_key: max_seq}`` across the given records."""
    heads: dict[str, int] = {}
    for rec in records:
        dev = str(rec.get("device_key") or "")
        seq = int(rec.get("seq") or 0)
        if dev and seq > heads.get(dev, 0):
            heads[dev] = seq
    return heads


def select_missing_records(
    local_records: list[dict[str, Any]], remote_heads: dict[str, int]
) -> list[dict[str, Any]]:
    """Return local records whose ``seq`` is strictly greater than the remote's head for that device."""
    missing = [
        rec
        for rec in local_records
        if int(rec.get("seq") or 0) > int(remote_heads.get(str(rec.get("device_key") or ""), 0))
    ]
    missing.sort(key=lambda r: (str(r.get("device_key") or ""), int(r.get("seq") or 0)))
    return missing


def verify_incoming_batch(
    incoming_records: list[dict[str, Any]],
    *,
    hill_id: str,
    admin_root_key: str,
    existing_heads: dict[str, int],
    existing_prev_hashes: dict[str, str],
    active_members: set[str],
    revoked_devices: set[str],
    device_bindings: dict[str, str],
    incoming_bindings: dict[str, dict[str, str]] | None = None,
    blobs: dict[str, str] | None = None,
) -> SyncBatchResult:
    """Verify an incoming delta batch and classify accepted vs rejected records."""
    result = SyncBatchResult(
        active_members=set(active_members),
        revoked_devices=set(revoked_devices),
    )
    heads = dict(existing_heads)
    prev_hashes = dict(existing_prev_hashes)
    bindings = dict(device_bindings)
    blob_map = blobs or {}

    # Absorb any cryptographically valid root -> device bindings supplied with the batch.
    for dev_key, meta in (incoming_bindings or {}).items():
        if not isinstance(meta, dict):
            continue
        root_pub = str(meta.get("root_pub") or "")
        x25519_pub = str(meta.get("x25519_pub") or "")
        dev_name = str(meta.get("device_name") or "")
        sig = str(meta.get("binding_sig") or "")
        if root_pub and verify_device_binding(root_pub, dev_key, x25519_pub, dev_name, sig):
            bindings[dev_key] = root_pub

    # Process the admin's device chain(s) first in strict seq order so Membership updates
    # take effect before verifying other members' device chains (each in strict seq order).
    ordered = sorted(
        incoming_records,
        key=lambda r: (
            0 if r.get("author_root_key") == admin_root_key else 1,
            str(r.get("device_key") or ""),
            int(r.get("seq") or 0),
        ),
    )

    for rec in ordered:
        rec_id = str(rec.get("record_hash") or f"seq:{rec.get('seq')}")
        if rec.get("hill_id") != hill_id:
            result.rejected.append((rec_id, "hill_id mismatch"))
            continue

        dev = str(rec.get("device_key") or "")
        seq = int(rec.get("seq") or 0)
        cur_head = heads.get(dev, 0)
        if seq <= cur_head:
            # Already have this sequence number; ignore duplicate idempotently.
            continue

        expected_seq = cur_head + 1
        expected_prev = prev_hashes.get(dev, GENESIS_PREV_HASH)

        ok, reason = verify_record(
            rec,
            expected_prev_hash=expected_prev,
            expected_seq=expected_seq,
            active_members=result.active_members,
            revoked_devices=result.revoked_devices,
            device_bindings=bindings,
        )
        if not ok:
            result.rejected.append((rec_id, reason))
            continue

        # Check detached content integrity if a content_hash and matching blob are present.
        content_hash = rec.get("content_hash")
        if (
            content_hash
            and content_hash in blob_map
            and not verify_content_blob(blob_map[content_hash], str(content_hash))
        ):
            result.rejected.append((rec_id, "detached content_hash mismatch"))
            continue

        # Update chain state for this device.
        heads[dev] = seq
        prev_hashes[dev] = str(rec["record_hash"])
        result.accepted.append(rec)

        # Apply signed admin Membership updates immediately to govern subsequent records.
        if rec["record_type"] == "Membership" and rec["author_root_key"] == admin_root_key:
            payload = rec["payload"]
            raw_members = payload.get("members")
            if isinstance(raw_members, list):
                result.active_members = {str(m) for m in raw_members if isinstance(m, str) and m}
                result.active_members.add(admin_root_key)
            raw_revoked = payload.get("revoked_devices")
            if isinstance(raw_revoked, list):
                result.revoked_devices.update(
                    str(d) for d in raw_revoked if isinstance(d, str) and d
                )
            result.membership_updates.append(rec)
        elif rec["record_type"] == "PageChange":
            result.page_changes.append(rec)

    return result
