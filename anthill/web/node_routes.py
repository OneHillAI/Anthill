"""FastAPI routes for the peer-to-peer Node pillar UI and authenticated P2P HTTP endpoints.

Mounted onto ``anthill.web.app`` via ``include_router``. Keeps ``app.py`` minimal while reusing
the app's DB sessions, templates, auth dependencies, WikiReview gate, and Sovereignty metrics.
"""

from __future__ import annotations

import json
import platform
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ..common.text import first_h1, slugify
from ..inference.ollama import OllamaBackend
from ..node import (
    DEFAULT_OPEN_TRUST_THRESHOLD,
    MAX_CONSULT_TOKENS,
    PEER_RATE_LIMIT_PER_HOUR,
)
from ..node import consult as node_consult
from ..node import identity as node_id
from ..node import lending as node_lending
from ..node import log as node_log
from ..node import pairing as node_pairing
from ..node import sync as node_sync
from ..node import transport as node_transport
from ..node import wiki_bridge as node_wiki_bridge
from ..wiki.workspace import Workspace, workspace_for
from . import audit, metrics
from .db import (
    NodeBlob,
    NodeConsultation,
    NodeDevice,
    NodeEgressLedger,
    NodeHill,
    NodeIdentity,
    NodeInvite,
    NodeLendingRule,
    NodePeer,
    NodeRecord,
    NodeSettings,
    OrgSettings,
    Team,
    TeamMembership,
    User,
    WikiReview,
)

# Runtime hooks (overridable by tests for hermetic two-instance E2E runs).
_peer_rate_limiter = node_lending.PeerRateLimiter(max_requests=PEER_RATE_LIMIT_PER_HOUR)
_seen_http_nonces: set[str] = set()
_client_transport_resolver: Callable[[str], httpx.BaseTransport | None] | None = None
_peer_backend_factory: Callable[[Session, int, int], Any] | None = None


def set_test_hooks(
    *,
    transport_resolver: Callable[[str], httpx.BaseTransport | None] | None = None,
    backend_factory: Callable[[Session, int, int], Any] | None = None,
) -> None:
    """Install or clear test hooks for hermetic multi-instance P2P tests."""
    global _client_transport_resolver, _peer_backend_factory
    _client_transport_resolver = transport_resolver
    _peer_backend_factory = backend_factory
    _seen_http_nonces.clear()


def _resolve_transport(endpoint_url: str) -> httpx.BaseTransport | None:
    return _client_transport_resolver(endpoint_url) if _client_transport_resolver else None


# -- Helper accessors over app.py dependencies (injected when building router) -


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_settings(db: Session, user_id: int) -> NodeSettings:
    row = db.query(NodeSettings).filter(NodeSettings.user_id == user_id).first()
    if row is None:
        row = NodeSettings(
            user_id=user_id,
            enabled=False,
            presence_visible=False,
            transport="bundle",
            public_endpoint="",
            open_trust_threshold=DEFAULT_OPEN_TRUST_THRESHOLD,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _ensure_identity(
    db: Session, user_id: int, *, device_name: str = ""
) -> tuple[NodeIdentity, NodeDevice]:
    ident = db.query(NodeIdentity).filter(NodeIdentity.user_id == user_id).first()
    if ident is None:
        root_kp = node_id.generate_ed25519_keypair()
        x_kp = node_id.generate_x25519_keypair()
        ident = NodeIdentity(
            user_id=user_id,
            root_pub_key=root_kp.public_b64,
            root_priv_key_enc=node_id.encrypt_secret(root_kp.private_b64),
            x25519_pub_key=x_kp.public_b64,
            x25519_priv_key_enc=node_id.encrypt_secret(x_kp.private_b64),
        )
        db.add(ident)
        db.commit()
        db.refresh(ident)

    dev = (
        db.query(NodeDevice)
        .filter(
            NodeDevice.user_id == user_id,
            NodeDevice.is_current.is_(True),
            NodeDevice.revoked_at.is_(None),
        )
        .first()
    )
    if dev is None:
        dev_kp = node_id.generate_ed25519_keypair()
        dname = device_name or f"{platform.node() or 'Anthill Desktop'}"
        root_priv = node_id.decrypt_secret(ident.root_priv_key_enc)
        binding_sig = node_id.sign_device_binding(
            root_priv, ident.root_pub_key, dev_kp.public_b64, ident.x25519_pub_key, dname
        )
        dev = NodeDevice(
            user_id=user_id,
            device_name=dname,
            device_pub_key=dev_kp.public_b64,
            device_priv_key_enc=node_id.encrypt_secret(dev_kp.private_b64),
            root_binding_sig=binding_sig,
            is_current=True,
        )
        db.add(dev)
        db.commit()
        db.refresh(dev)
    return ident, dev


def _ensure_default_hill(
    db: Session, user_id: int, ident: NodeIdentity, dev: NodeDevice
) -> NodeHill:
    hill = db.query(NodeHill).filter(NodeHill.user_id == user_id).first()
    if hill is None:
        hill = NodeHill(
            user_id=user_id,
            hill_id=f"pilot-{slugify(ident.root_pub_key[:10])}",
            name="Anthill Core Pilot",
            scope="private-group",
            admin_root_key=ident.root_pub_key,
            membership_seq=0,
        )
        db.add(hill)
        db.commit()
        db.refresh(hill)
        _append_signed_record(
            db,
            user_id=user_id,
            ident=ident,
            dev=dev,
            hill=hill,
            record_type="Membership",
            payload={
                "action": "init",
                "hill_name": hill.name,
                "members": [ident.root_pub_key],
                "revoked_devices": [],
            },
        )
        hill.membership_seq = 1
        db.commit()
    return hill


def _local_model_info(db: Session, org_id: int) -> tuple[str, str]:
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    url = (getattr(cfg, "ollama_url", "") or "http://127.0.0.1:11434").strip()
    model = (getattr(cfg, "ollama_model", "") or "qwen2.5:3b").strip()
    return url, model


def _build_local_backend(db: Session, org_id: int, user_id: int) -> Any:
    if _peer_backend_factory is not None:
        return _peer_backend_factory(db, org_id, user_id)
    url, model = _local_model_info(db, org_id)
    return OllamaBackend(url, model)


def _user_workspaces(db: Session, user_id: int, org_id: int) -> list[tuple[str, Workspace, str]]:
    out: list[tuple[str, Workspace, str]] = [
        ("personal", workspace_for("personal", user_id=user_id, org_id=org_id), ""),
    ]
    teams = (
        db.query(Team)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .filter(
            TeamMembership.user_id == user_id,
            TeamMembership.status == "active",
            Team.org_id == org_id,
        )
        .all()
    )
    for t in teams:
        out.append(("team", workspace_for("team", team_id=t.id, org_id=org_id), str(t.name)))
    out.append(("org", workspace_for("org", org_id=org_id), ""))
    return out


def _latest_device_head(
    db: Session, user_id: int, hill_id: str, device_key: str
) -> tuple[int, str]:
    row = (
        db.query(NodeRecord)
        .filter(
            NodeRecord.user_id == user_id,
            NodeRecord.hill_id == hill_id,
            NodeRecord.device_key == device_key,
        )
        .order_by(NodeRecord.seq.desc())
        .first()
    )
    if row is None:
        return 0, node_log.GENESIS_PREV_HASH
    return int(row.seq), str(row.record_hash)


def _append_signed_record(
    db: Session,
    *,
    user_id: int,
    ident: NodeIdentity,
    dev: NodeDevice,
    hill: NodeHill,
    record_type: str,
    payload: dict[str, Any],
    model: str = "",
    content_body: str | None = None,
    payload_type: str | None = None,
) -> dict[str, Any]:
    seq, prev_hash = _latest_device_head(db, user_id, hill.hill_id, dev.device_pub_key)
    dev_priv = node_id.decrypt_secret(dev.device_priv_key_enc)
    p_type = payload_type or ("plain" if hill.scope == "open" else "encrypted")
    envelope = node_log.create_record(
        record_type=record_type,
        scope=hill.scope,
        hill_id=hill.hill_id,
        author_root_key=ident.root_pub_key,
        device_key=dev.device_pub_key,
        device_priv_b64=dev_priv,
        seq=seq + 1,
        prev_hash=prev_hash,
        payload=payload,
        payload_type=p_type,
        model=model,
        content_body=content_body,
    )
    db.add(
        NodeRecord(
            user_id=user_id,
            hill_id=hill.hill_id,
            record_hash=envelope["record_hash"],
            prev_hash=envelope["prev_hash"],
            seq=envelope["seq"],
            author_root_key=envelope["author_root_key"],
            device_key=envelope["device_key"],
            record_type=envelope["record_type"],
            scope=envelope["scope"],
            payload_type=envelope["payload_type"],
            model=envelope["model"],
            content_ref=envelope["content_ref"],
            content_hash=envelope["content_hash"],
            envelope_json=node_log.canonical_json(envelope),
        )
    )
    if content_body is not None and envelope["content_hash"]:
        existing_blob = (
            db.query(NodeBlob)
            .filter(
                NodeBlob.user_id == user_id,
                NodeBlob.content_hash == envelope["content_hash"],
            )
            .first()
        )
        if existing_blob is None:
            db.add(
                NodeBlob(
                    user_id=user_id,
                    content_hash=envelope["content_hash"],
                    content_text=content_body,
                )
            )
    db.commit()
    return envelope


def _log_egress_usage(
    db: Session,
    *,
    user_id: int,
    org_id: int | None,
    ident: NodeIdentity,
    dev: NodeDevice,
    hill: NodeHill,
    direction: str,
    peer_name: str,
    peer_root_key: str,
    model: str,
    outcome: str,
    count_as_peer_metric: bool = False,
) -> None:
    """Record zero-content Usage record + NodeEgressLedger row + optional QueryMetric (Rule 2)."""
    usage_env = _append_signed_record(
        db,
        user_id=user_id,
        ident=ident,
        dev=dev,
        hill=hill,
        record_type="Usage",
        model=model,
        payload={
            "direction": direction,
            "peer_name": peer_name,
            "peer_root_key": peer_root_key,
            "scope": hill.scope,
            "outcome": outcome,
        },
    )
    db.add(
        NodeEgressLedger(
            user_id=user_id,
            direction=direction,
            peer_name=peer_name,
            peer_root_key=peer_root_key,
            model=model or "n/a",
            scope=hill.scope,
            outcome=outcome,
            record_hash=usage_env["record_hash"],
        )
    )
    db.commit()
    if count_as_peer_metric:
        metrics.record(
            db,
            org_id=org_id,
            node_id="peer-p2p",
            cache_hit=False,
            source="peer",
            duration_ms=None,
            model=model or "peer",
        )


def _hill_records_as_dicts(db: Session, user_id: int, hill_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(NodeRecord)
        .filter(NodeRecord.user_id == user_id, NodeRecord.hill_id == hill_id)
        .order_by(NodeRecord.seq.asc(), NodeRecord.id.asc())
        .all()
    )
    return [json.loads(r.envelope_json) for r in rows]


def _hill_blobs_map(db: Session, user_id: int, records: list[dict[str, Any]]) -> dict[str, str]:
    hashes = {str(r["content_hash"]) for r in records if r.get("content_hash")}
    if not hashes:
        return {}
    rows = (
        db.query(NodeBlob)
        .filter(
            NodeBlob.user_id == user_id,
            NodeBlob.content_hash.in_(hashes),
            NodeBlob.erased_at.is_(None),
            NodeBlob.content_text.isnot(None),
        )
        .all()
    )
    return {str(b.content_hash): str(b.content_text) for b in rows if b.content_text is not None}


def _active_member_keys(
    db: Session, user_id: int, hill: NodeHill, ident: NodeIdentity
) -> tuple[set[str], set[str], dict[str, str]]:
    """Compute (active_member_root_keys, revoked_device_keys, device_key->root_key bindings)."""
    members: set[str] = {ident.root_pub_key, hill.admin_root_key}
    revoked_devs: set[str] = set()
    bindings: dict[str, str] = {}

    for d in db.query(NodeDevice).filter(NodeDevice.user_id == user_id).all():
        bindings[d.device_pub_key] = ident.root_pub_key
        if d.revoked_at is not None:
            revoked_devs.add(d.device_pub_key)

    for p in (
        db.query(NodePeer)
        .filter(NodePeer.user_id == user_id, NodePeer.hill_id == hill.hill_id)
        .all()
    ):
        bindings[p.device_pub_key] = p.root_pub_key
        if p.revoked:
            revoked_devs.add(p.device_pub_key)
        elif p.verified:
            members.add(p.root_pub_key)

    # Also fold any signed admin Membership records already in the log.
    recs = _hill_records_as_dicts(db, user_id, hill.hill_id)
    log_members, log_revoked, _ = node_log.resolve_membership(
        recs, hill.admin_root_key, device_bindings=bindings
    )
    if any(
        r.get("record_type") == "Membership" and r.get("author_root_key") == hill.admin_root_key
        for r in recs
    ):
        # Admin Membership record is authoritative when present.
        members = set(log_members)
        revoked_devs |= log_revoked

    return members, revoked_devs, bindings


def _local_device_bindings_bundle(
    db: Session, user_id: int, ident: NodeIdentity, hill_id: str
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for d in db.query(NodeDevice).filter(NodeDevice.user_id == user_id).all():
        out[d.device_pub_key] = {
            "root_pub": ident.root_pub_key,
            "x25519_pub": ident.x25519_pub_key,
            "device_name": d.device_name,
            "binding_sig": d.root_binding_sig,
        }
    for p in (
        db.query(NodePeer)
        .filter(
            NodePeer.user_id == user_id,
            NodePeer.hill_id == hill_id,
            NodePeer.verified.is_(True),
            NodePeer.revoked.is_(False),
        )
        .all()
    ):
        if p.binding_sig:
            out[p.device_pub_key] = {
                "root_pub": p.root_pub_key,
                "x25519_pub": p.x25519_pub_key,
                "device_name": p.device_name,
                "binding_sig": p.binding_sig,
            }
    return out


def _ingest_verified_sync_batch(
    db: Session,
    *,
    user_id: int,
    org_id: int,
    hill: NodeHill,
    ident: NodeIdentity,
    incoming_records: list[dict[str, Any]],
    incoming_blobs: dict[str, str],
    incoming_bindings: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Verify incoming records, persist valid ones, and bridge PageChange -> WikiReview (Rule 4)."""
    existing = _hill_records_as_dicts(db, user_id, hill.hill_id)
    existing_heads = node_sync.compute_heads(existing)
    existing_prev: dict[str, str] = {}
    for r in existing:
        dev = str(r.get("device_key") or "")
        seq = int(r.get("seq") or 0)
        if dev and seq == existing_heads.get(dev):
            existing_prev[dev] = str(r["record_hash"])

    active_members, revoked_devs, bindings = _active_member_keys(db, user_id, hill, ident)
    batch = node_sync.verify_incoming_batch(
        incoming_records,
        hill_id=hill.hill_id,
        admin_root_key=hill.admin_root_key,
        existing_heads=existing_heads,
        existing_prev_hashes=existing_prev,
        active_members=active_members,
        revoked_devices=revoked_devs,
        device_bindings=bindings,
        incoming_bindings=incoming_bindings,
        blobs=incoming_blobs,
    )

    # Persist accepted records and their detached blobs.
    for rec in batch.accepted:
        already = (
            db.query(NodeRecord)
            .filter(
                NodeRecord.user_id == user_id,
                NodeRecord.record_hash == rec["record_hash"],
            )
            .first()
        )
        if already is None:
            db.add(
                NodeRecord(
                    user_id=user_id,
                    hill_id=hill.hill_id,
                    record_hash=rec["record_hash"],
                    prev_hash=rec["prev_hash"],
                    seq=rec["seq"],
                    author_root_key=rec["author_root_key"],
                    device_key=rec["device_key"],
                    record_type=rec["record_type"],
                    scope=rec["scope"],
                    payload_type=rec["payload_type"],
                    model=rec.get("model") or "",
                    content_ref=rec.get("content_ref"),
                    content_hash=rec.get("content_hash"),
                    envelope_json=node_log.canonical_json(rec),
                )
            )
        c_hash = rec.get("content_hash")
        if c_hash and c_hash in incoming_blobs:
            blob_row = (
                db.query(NodeBlob)
                .filter(NodeBlob.user_id == user_id, NodeBlob.content_hash == c_hash)
                .first()
            )
            if blob_row is None:
                db.add(
                    NodeBlob(
                        user_id=user_id,
                        content_hash=str(c_hash),
                        content_text=incoming_blobs[str(c_hash)],
                    )
                )

    # Update local peer revocation state if an admin Membership update removed a member.
    if batch.membership_updates:
        latest_m = max(batch.membership_updates, key=lambda r: int(r["seq"]))
        hill.membership_seq = max(hill.membership_seq, int(latest_m["seq"]))
        for p in (
            db.query(NodePeer)
            .filter(NodePeer.user_id == user_id, NodePeer.hill_id == hill.hill_id)
            .all()
        ):
            if (
                p.root_pub_key not in batch.active_members
                or p.device_pub_key in batch.revoked_devices
            ):
                p.revoked = True

    db.commit()

    # Bridge every inbound PageChange into WikiReview (Rule 4: NEVER write directly to wiki).
    settings = _ensure_settings(db, user_id)
    ws = workspace_for("personal", user_id=user_id, org_id=org_id)
    proposals_created = 0
    for rec in batch.page_changes:
        if rec["author_root_key"] == ident.root_pub_key:
            continue
        existing_rev = (
            db.query(WikiReview)
            .filter(
                WikiReview.org_id == org_id,
                WikiReview.peer_record_hash == rec["record_hash"],
            )
            .first()
        )
        if existing_rev is not None:
            continue
        c_hash = str(rec.get("content_hash") or "")
        body = incoming_blobs.get(c_hash) or str((rec.get("payload") or {}).get("excerpt") or "")
        if not body.strip():
            continue
        peer_row = (
            db.query(NodePeer)
            .filter(
                NodePeer.user_id == user_id,
                NodePeer.hill_id == hill.hill_id,
                NodePeer.root_pub_key == rec["author_root_key"],
            )
            .first()
        )
        signer_name = (
            peer_row.display_name if peer_row else node_id.key_fingerprint(rec["author_root_key"])
        )
        signer_karma = int(peer_row.karma) if peer_row else 0
        proposal = node_wiki_bridge.evaluate_inbound_page_change(
            ws,
            record=rec,
            content=body,
            signer_name=signer_name,
            signer_karma=signer_karma,
            open_trust_threshold=int(settings.open_trust_threshold),
        )
        db.add(
            WikiReview(
                org_id=org_id,
                proposed_by=user_id,
                provenance_hash=proposal.record_hash[:64],
                slug=proposal.slug,
                kind="page",
                content=proposal.content,
                diff_summary=proposal.diff_summary,
                target_scope="personal",
                outline=proposal.outline,
                flags=json.dumps(proposal.flags),
                status="pending",
                peer_record_hash=proposal.record_hash,
                peer_state=proposal.peer_state,
                peer_hill_id=proposal.hill_id,
                peer_signer_key=proposal.signer_root_key,
                peer_signer_name=proposal.signer_name,
            )
        )
        proposals_created += 1

    db.commit()
    return {
        "accepted": len(batch.accepted),
        "rejected": len(batch.rejected),
        "rejected_reasons": batch.rejected,
        "proposals_created": proposals_created,
    }


def _sign_membership_update(
    db: Session, *, user_id: int, ident: NodeIdentity, dev: NodeDevice, hill: NodeHill, action: str
) -> dict[str, Any]:
    active_peers = (
        db.query(NodePeer)
        .filter(
            NodePeer.user_id == user_id,
            NodePeer.hill_id == hill.hill_id,
            NodePeer.verified.is_(True),
            NodePeer.revoked.is_(False),
        )
        .all()
    )
    revoked_devs = [
        d.device_pub_key
        for d in db.query(NodeDevice)
        .filter(NodeDevice.user_id == user_id, NodeDevice.revoked_at.isnot(None))
        .all()
    ]
    revoked_devs += [
        p.device_pub_key
        for p in db.query(NodePeer)
        .filter(
            NodePeer.user_id == user_id,
            NodePeer.hill_id == hill.hill_id,
            NodePeer.revoked.is_(True),
        )
        .all()
    ]
    members = sorted({ident.root_pub_key, *(p.root_pub_key for p in active_peers)})
    env = _append_signed_record(
        db,
        user_id=user_id,
        ident=ident,
        dev=dev,
        hill=hill,
        record_type="Membership",
        payload={
            "action": action,
            "hill_name": hill.name,
            "members": members,
            "revoked_devices": sorted(set(revoked_devs)),
        },
    )
    hill.membership_seq = int(env["seq"])
    db.commit()
    return env


def _allowed_lending_areas(
    db: Session,
    user_id: int,
    hill: NodeHill,
    areas: list[node_lending.WikiAreaInfo],
) -> set[str]:
    rules = {
        r.area_id: r
        for r in db.query(NodeLendingRule)
        .filter(NodeLendingRule.user_id == user_id, NodeLendingRule.hill_id == hill.hill_id)
        .all()
    }
    allowed: set[str] = set()
    for area in areas:
        if area.locked:
            continue
        rule = rules.get(area.area_id)
        if rule is not None:
            if rule.allowed and not rule.locked:
                allowed.add(area.area_id)
        elif node_lending.default_allowed_for_hill(area.area_id, hill.scope, area.locked):
            allowed.add(area.area_id)
    return allowed


def _resolve_active_node_owner(db: Session) -> tuple[User, NodeSettings, NodeIdentity, NodeDevice]:
    """Find the enabled local node owner on this desktop instance (Rule 12 gate)."""
    settings = db.query(NodeSettings).filter(NodeSettings.enabled.is_(True)).first()
    if settings is None:
        raise HTTPException(
            status_code=403, detail="Node peer-to-peer is disabled on this desktop (Rule 12)."
        )
    user = db.query(User).filter(User.id == settings.user_id).first()
    if user is None:
        raise HTTPException(status_code=403, detail="Node owner account not found.")
    ident, dev = _ensure_identity(db, int(user.id))
    return user, settings, ident, dev


def _authenticate_peer_request(
    db: Session,
    *,
    owner_user_id: int,
    hill_id: str,
    method: str,
    path: str,
    body_bytes: bytes,
    headers: Any,
) -> tuple[NodeHill, NodePeer]:
    hill = (
        db.query(NodeHill)
        .filter(NodeHill.user_id == owner_user_id, NodeHill.hill_id == hill_id)
        .first()
    )
    if hill is None:
        raise HTTPException(status_code=404, detail="Unknown Hill.")

    peers = (
        db.query(NodePeer)
        .filter(
            NodePeer.user_id == owner_user_id,
            NodePeer.hill_id == hill_id,
            NodePeer.verified.is_(True),
        )
        .all()
    )
    pinned_by_root: dict[str, set[str]] = {}
    revoked_devs: set[str] = set()
    for p in peers:
        if p.revoked:
            revoked_devs.add(p.device_pub_key)
        else:
            pinned_by_root.setdefault(p.root_pub_key, set()).add(p.device_pub_key)

    ok, reason, root_key, dev_key = node_transport.verify_http_request(
        method,
        path,
        body_bytes,
        headers,
        pinned_device_keys_by_root=pinned_by_root,
        revoked_devices=revoked_devs,
        seen_nonces=_seen_http_nonces,
    )
    if not ok:
        raise HTTPException(status_code=403, detail=f"Peer authentication failed: {reason}")

    peer = next(
        (
            p
            for p in peers
            if p.root_pub_key == root_key and p.device_pub_key == dev_key and not p.revoked
        ),
        None,
    )
    if peer is None:
        raise HTTPException(status_code=403, detail="Peer is not an active member of this Hill.")
    peer.status = "online"
    peer.last_seen_at = _now_utc()
    db.commit()
    return hill, peer


def nav_node_summary(db: Session, user_id: int) -> dict[str, Any]:
    """Lightweight sidebar context helper for ``_nav_context`` (never creates rows)."""
    row = db.query(NodeSettings).filter(NodeSettings.user_id == user_id).first()
    if row is None or not row.enabled:
        return {"enabled": False, "online_peers": 0}
    online = (
        db.query(NodePeer)
        .filter(
            NodePeer.user_id == user_id,
            NodePeer.verified.is_(True),
            NodePeer.revoked.is_(False),
            NodePeer.status == "online",
        )
        .count()
    )
    return {"enabled": True, "online_peers": online}


def record_peer_review_decision(
    db: Session, *, user_id: int, rev: WikiReview, accepted: bool
) -> None:
    """Write a signed Acceptance or Deletion record when a human decides a peer WikiReview."""
    if not getattr(rev, "peer_record_hash", ""):
        return
    hill_id = getattr(rev, "peer_hill_id", "") or "anthill-core-pilot"
    hill = (
        db.query(NodeHill).filter(NodeHill.user_id == user_id, NodeHill.hill_id == hill_id).first()
    )
    if hill is None:
        return
    ident, dev = _ensure_identity(db, user_id)
    _append_signed_record(
        db,
        user_id=user_id,
        ident=ident,
        dev=dev,
        hill=hill,
        record_type="Acceptance" if accepted else "Deletion",
        payload={
            "target_record_hash": rev.peer_record_hash,
            "slug": rev.slug,
            "decision": "accepted" if accepted else "dismissed",
        },
    )


def build_node_router(
    *,
    get_db: Callable[[], Session],
    require_user: Callable[..., dict[str, Any]],
    templates: Any,
) -> APIRouter:
    router = APIRouter()

    # -- Desktop UI: Peers & Hills (/peers) ------------------------------------

    @router.get("/peers", response_class=HTMLResponse)
    def peers_page(
        request: Request,
        tab: str = "connections",
        hill: str = "all",
        msg: str = "",
        err: str = "",
        recovery: str = "",
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        valid_tabs = {"connections", "lending", "log", "identity"}
        active_tab = tab if tab in valid_tabs else "connections"

        if not settings.enabled:
            return templates.TemplateResponse(
                request,
                "peers.html",
                {
                    "request": request,
                    "user": user,
                    "settings": settings,
                    "active_tab": active_tab,
                    "msg": msg,
                    "err": err,
                },
            )

        ident, dev = _ensure_identity(db, uid)
        default_hill = _ensure_default_hill(db, uid, ident, dev)
        hills = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid)
            .order_by(NodeHill.created_at.asc())
            .all()
        )
        hill_by_id = {h.hill_id: h for h in hills}
        peers_q = db.query(NodePeer).filter(
            NodePeer.user_id == uid, NodePeer.verified.is_(True), NodePeer.revoked.is_(False)
        )
        all_peers = peers_q.order_by(NodePeer.created_at.desc()).all()
        filtered_peers = [p for p in all_peers if hill == "all" or p.hill_id == hill]
        online_count = sum(1 for p in all_peers if p.status == "online")

        invites = (
            db.query(NodeInvite)
            .filter(
                NodeInvite.user_id == uid,
                NodeInvite.status.in_(("pending", "awaiting_sas")),
            )
            .order_by(NodeInvite.created_at.desc())
            .all()
        )
        invites_view = []
        for inv in invites:
            peer_meta = json.loads(inv.peer_payload_json) if inv.peer_payload_json else {}
            invites_view.append({"invite": inv, "peer_meta": peer_meta})

        # Knowledge lending matrix.
        workspaces = _user_workspaces(db, uid, org_id)
        areas = node_lending.discover_wiki_areas(workspaces)
        lending_matrix: dict[str, set[str]] = {
            h.hill_id: _allowed_lending_areas(db, uid, h, areas) for h in hills
        }

        # Signed record log.
        records_rows = (
            db.query(NodeRecord)
            .filter(NodeRecord.user_id == uid)
            .order_by(NodeRecord.id.desc())
            .limit(80)
            .all()
        )
        records_view = []
        for r in records_rows:
            env = json.loads(r.envelope_json)
            records_view.append(
                {
                    "row": r,
                    "envelope": env,
                    "pretty_json": json.dumps(env, indent=2, sort_keys=True),
                    "author_fp": node_id.key_fingerprint(r.author_root_key),
                    "summary": _record_summary(env),
                }
            )

        # Devices & egress ledger.
        devices = (
            db.query(NodeDevice)
            .filter(NodeDevice.user_id == uid)
            .order_by(NodeDevice.is_current.desc(), NodeDevice.created_at.asc())
            .all()
        )
        egress_rows = (
            db.query(NodeEgressLedger)
            .filter(NodeEgressLedger.user_id == uid)
            .order_by(NodeEgressLedger.id.desc())
            .limit(60)
            .all()
        )
        personal_ws = workspace_for("personal", user_id=uid, org_id=org_id)
        local_pages = [
            {"slug": p.stem, "title": first_h1(p.read_text(encoding="utf-8")) or p.stem}
            for p in (personal_ws.pages() if personal_ws.wiki.exists() else [])
        ]

        # Compute per-peer synced head seq.
        all_rec_dicts = [json.loads(r.envelope_json) for r in records_rows]
        heads_by_dev = node_sync.compute_heads(all_rec_dicts)

        _, local_model = _local_model_info(db, org_id)
        recovery_words = (
            node_id.export_recovery_kit(node_id.decrypt_secret(ident.root_priv_key_enc))
            if recovery == "show"
            else ""
        )

        return templates.TemplateResponse(
            request,
            "peers.html",
            {
                "request": request,
                "user": user,
                "settings": settings,
                "active_tab": active_tab,
                "selected_hill": hill,
                "default_hill": default_hill,
                "hills": hills,
                "hill_by_id": hill_by_id,
                "all_peers": all_peers,
                "peers": filtered_peers,
                "online_count": online_count,
                "invites": invites_view,
                "areas": areas,
                "lending_matrix": lending_matrix,
                "records": records_view,
                "heads_by_dev": heads_by_dev,
                "ident": ident,
                "current_device": dev,
                "root_fp": node_id.key_fingerprint(ident.root_pub_key),
                "device_fp": node_id.key_fingerprint(dev.device_pub_key),
                "devices": devices,
                "egress_rows": egress_rows,
                "local_pages": local_pages,
                "local_model": local_model,
                "recovery_words": recovery_words,
                "max_consult_tokens": MAX_CONSULT_TOKENS,
                "rate_limit_hr": PEER_RATE_LIMIT_PER_HOUR,
                "key_fp": node_id.key_fingerprint,
                "msg": msg,
                "err": err,
            },
        )

    @router.post("/peers/toggle")
    def peers_toggle(
        request: Request,
        action: str = Form("enabled"),
        transport: str = Form(""),
        public_endpoint: str = Form(""),
        tab: str = Form("connections"),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        msg = "Node settings updated."

        if action == "enabled":
            settings.enabled = not settings.enabled
            if settings.enabled:
                ident, dev = _ensure_identity(db, uid)
                _ensure_default_hill(db, uid, ident, dev)
                msg = "Node peer-to-peer enabled (peer endpoint active, 0 tools)."
            else:
                settings.presence_visible = False
                msg = "Node peer-to-peer disabled - Anthill runs strictly locally."
        elif action == "presence":
            settings.presence_visible = not settings.presence_visible
            msg = (
                "Signed presence visible inside joined Hills."
                if settings.presence_visible
                else "Presence hidden (peers see you as offline)."
            )
        elif action == "transport" and transport in ("bundle", "lan"):
            settings.transport = transport
            msg = f"Transport switched to {'Encrypted file bundle' if transport == 'bundle' else 'Local network (LAN HTTP)'}."
        elif action == "endpoint":
            settings.public_endpoint = public_endpoint.strip().rstrip("/")
            msg = "Direct peer endpoint URL saved."

        settings.updated_at = _now_utc()
        db.commit()
        audit.log(
            db,
            "node.settings",
            f"action={action} enabled={settings.enabled}",
            org_id=org_id,
            user_id=uid,
        )
        return RedirectResponse(f"/peers?tab={tab}&msg={quote_plus(msg)}", status_code=303)

    @router.post("/peers/hills/create")
    def create_hill(
        name: str = Form(...),
        scope: str = Form("private-group"),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        settings = _ensure_settings(db, uid)
        if not settings.enabled:
            raise HTTPException(status_code=403, detail="Enable Node peer-to-peer first.")
        if scope not in ("private-group", "internal", "open"):
            raise HTTPException(status_code=400, detail="Invalid Hill scope.")
        ident, dev = _ensure_identity(db, uid)
        clean_name = name.strip()[:100]
        if not clean_name:
            return RedirectResponse("/peers?err=Hill+name+is+required.", status_code=303)
        hid = f"{slugify(clean_name)}-{secrets.token_hex(3)}"
        hill = NodeHill(
            user_id=uid,
            hill_id=hid,
            name=clean_name,
            scope=scope,
            admin_root_key=ident.root_pub_key,
            membership_seq=0,
        )
        db.add(hill)
        db.commit()
        db.refresh(hill)
        _sign_membership_update(db, user_id=uid, ident=ident, dev=dev, hill=hill, action="init")
        return RedirectResponse(
            f"/peers?msg={quote_plus(f'Created Hill {clean_name} ({scope}).')}", status_code=303
        )

    # -- Pairing & SAS Verification --------------------------------------------

    @router.post("/peers/invites/create")
    def create_invite(
        request: Request,
        hill_id: str = Form(...),
        endpoint_url: str = Form(""),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        if not settings.enabled:
            raise HTTPException(status_code=403, detail="Enable Node peer-to-peer first.")
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill).filter(NodeHill.user_id == uid, NodeHill.hill_id == hill_id).first()
        )
        if hill is None or hill.admin_root_key != ident.root_pub_key:
            return RedirectResponse(
                "/peers?err=Only+the+Hill+admin+can+create+invites.", status_code=303
            )

        me = db.query(User).filter(User.id == uid).first()
        inviter_name = str(me.display_name or me.email) if me else f"User {uid}"
        _, local_model = _local_model_info(db, org_id)
        ep = endpoint_url.strip().rstrip("/") or settings.public_endpoint
        if endpoint_url.strip():
            settings.public_endpoint = ep
        root_priv = node_id.decrypt_secret(ident.root_priv_key_enc)
        token, env = node_pairing.create_invite_token(
            hill_id=hill.hill_id,
            hill_name=hill.name,
            scope=hill.scope,
            admin_root_key=hill.admin_root_key,
            inviter_name=inviter_name,
            inviter_root_key=ident.root_pub_key,
            inviter_root_priv_b64=root_priv,
            inviter_device_key=dev.device_pub_key,
            inviter_device_name=dev.device_name,
            inviter_x25519_key=ident.x25519_pub_key,
            inviter_binding_sig=dev.root_binding_sig,
            inviter_model=local_model,
            endpoint_url=ep,
        )
        inv = NodeInvite(
            user_id=uid,
            hill_id=hill.hill_id,
            hill_name=hill.name,
            scope=hill.scope,
            role="inviter",
            nonce=str(env["nonce"]),
            token=token,
            status="pending",
            expires_at=datetime.fromisoformat(str(env["expires_at"])),
        )
        db.add(inv)
        db.commit()
        return RedirectResponse(
            "/peers?msg=One-time+pairing+invite+created+(expires+in+10+min).", status_code=303
        )

    @router.post("/peers/invites/join")
    def join_with_invite(
        token: str = Form(...),
        my_endpoint_url: str = Form(""),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        if not settings.enabled:
            raise HTTPException(status_code=403, detail="Enable Node peer-to-peer first.")
        ident, dev = _ensure_identity(db, uid)
        try:
            env = node_pairing.parse_invite_token(token)
        except node_pairing.PairingError as exc:
            return RedirectResponse(f"/peers?err={quote_plus(str(exc))}", status_code=303)

        if env["inviter_root_key"] == ident.root_pub_key:
            return RedirectResponse(
                "/peers?err=Cannot+pair+a+desktop+with+itself.", status_code=303
            )

        sas = node_pairing.compute_sas_code(
            str(env["inviter_root_key"]), ident.root_pub_key, str(env["nonce"])
        )
        me = db.query(User).filter(User.id == uid).first()
        my_name = str(me.display_name or me.email) if me else f"User {uid}"
        _, local_model = _local_model_info(db, org_id)
        my_ep = my_endpoint_url.strip().rstrip("/") or settings.public_endpoint
        if my_endpoint_url.strip():
            settings.public_endpoint = my_ep

        root_priv = node_id.decrypt_secret(ident.root_priv_key_enc)
        join_resp = node_pairing.create_join_response(
            nonce=str(env["nonce"]),
            hill_id=str(env["hill_id"]),
            joiner_name=my_name,
            joiner_root_key=ident.root_pub_key,
            joiner_root_priv_b64=root_priv,
            joiner_device_key=dev.device_pub_key,
            joiner_device_name=dev.device_name,
            joiner_x25519_key=ident.x25519_pub_key,
            joiner_binding_sig=dev.root_binding_sig,
            joiner_model=local_model,
            endpoint_url=my_ep,
        )

        # If the invite carries a direct LAN/Tailscale URL, push join_response so the inviter
        # immediately sees the matching 6-digit SAS code on their screen too.
        live_notified = False
        inviter_ep = str(env.get("endpoint_url") or "").strip().rstrip("/")
        if inviter_ep:
            try:
                dev_priv = node_id.decrypt_secret(dev.device_priv_key_enc)
                client = node_transport.HttpPeerTransport(
                    inviter_ep,
                    root_key=ident.root_pub_key,
                    device_key=dev.device_pub_key,
                    device_priv_b64=dev_priv,
                    client_transport=_resolve_transport(inviter_ep),
                )
                client.send_pair_request(join_resp)
                live_notified = True
            except node_transport.TransportError:
                live_notified = False

        inv = NodeInvite(
            user_id=uid,
            hill_id=str(env["hill_id"]),
            hill_name=str(env["hill_name"]),
            scope=str(env["scope"]),
            role="joiner",
            nonce=str(env["nonce"]),
            token=token.strip(),
            sas_code=sas,
            peer_payload_json=json.dumps(
                {"inviter": env, "join_response": join_resp, "live_notified": live_notified}
            ),
            status="awaiting_sas",
            expires_at=datetime.fromisoformat(str(env["expires_at"])),
        )
        db.add(inv)
        db.commit()
        note = (
            f"Verify short code {sas} matches on {env['inviter_name']}'s desktop."
            if live_notified
            else f"Verify short code {sas}. Share your offline join response with {env['inviter_name']}."
        )
        return RedirectResponse(f"/peers?msg={quote_plus(note)}", status_code=303)

    @router.post("/peers/invites/{invite_id}/attach-response")
    def attach_offline_join_response(
        invite_id: int,
        response_json: str = Form(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        inv = (
            db.query(NodeInvite)
            .filter(NodeInvite.id == invite_id, NodeInvite.user_id == uid)
            .first()
        )
        if inv is None or inv.role != "inviter":
            raise HTTPException(status_code=404, detail="Invite not found.")
        ident, _ = _ensure_identity(db, uid)
        try:
            resp = node_pairing.verify_join_response(
                json.loads(response_json), expected_nonce=inv.nonce
            )
        except Exception as exc:
            return RedirectResponse(f"/peers?err={quote_plus(str(exc))}", status_code=303)

        inv.sas_code = node_pairing.compute_sas_code(
            ident.root_pub_key, str(resp["joiner_root_key"]), inv.nonce
        )
        inv.peer_payload_json = json.dumps({"joiner": resp})
        inv.status = "awaiting_sas"
        db.commit()
        return RedirectResponse(
            f"/peers?msg={quote_plus(f'Join response verified. Confirm SAS code {inv.sas_code} matches.')}",
            status_code=303,
        )

    @router.post("/peers/invites/{invite_id}/confirm")
    def confirm_invite(
        invite_id: int,
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        inv = (
            db.query(NodeInvite)
            .filter(NodeInvite.id == invite_id, NodeInvite.user_id == uid)
            .first()
        )
        if inv is None or inv.status != "awaiting_sas":
            return RedirectResponse(
                "/peers?err=No+pending+SAS+handshake+to+confirm.", status_code=303
            )

        ident, dev = _ensure_identity(db, uid)
        meta = json.loads(inv.peer_payload_json or "{}")

        if inv.role == "inviter":
            joiner = meta.get("joiner") or {}
            hill = (
                db.query(NodeHill)
                .filter(NodeHill.user_id == uid, NodeHill.hill_id == inv.hill_id)
                .first()
            )
            if hill is None:
                raise HTTPException(status_code=404, detail="Hill not found.")
            peer = _upsert_verified_peer(
                db,
                user_id=uid,
                hill_id=hill.hill_id,
                display_name=str(joiner["joiner_name"]),
                device_name=str(joiner["joiner_device_name"]),
                root_pub_key=str(joiner["joiner_root_key"]),
                device_pub_key=str(joiner["joiner_device_key"]),
                x25519_pub_key=str(joiner["joiner_x25519_key"]),
                binding_sig=str(joiner["joiner_binding_sig"]),
                endpoint_url=str(joiner.get("endpoint_url") or ""),
                model_label=str(joiner.get("joiner_model") or "local"),
                sas_code=inv.sas_code,
            )
            _sign_membership_update(
                db, user_id=uid, ident=ident, dev=dev, hill=hill, action=f"add:{peer.display_name}"
            )
            inv.status = "confirmed"
            db.commit()
            return RedirectResponse(
                f"/peers?msg={quote_plus(f'Paired with {peer.display_name} (SAS {inv.sas_code}). Signed Membership updated.')}",
                status_code=303,
            )

        # Joiner side: create/update local Hill copy + pin inviter key.
        inviter = meta.get("inviter") or {}
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == inv.hill_id)
            .first()
        )
        if hill is None:
            hill = NodeHill(
                user_id=uid,
                hill_id=inv.hill_id,
                name=inv.hill_name or str(inviter.get("hill_name") or inv.hill_id),
                scope=inv.scope or str(inviter.get("scope") or "private-group"),
                admin_root_key=str(inviter["admin_root_key"]),
                membership_seq=0,
            )
            db.add(hill)
            db.commit()
            db.refresh(hill)
        peer = _upsert_verified_peer(
            db,
            user_id=uid,
            hill_id=hill.hill_id,
            display_name=str(inviter["inviter_name"]),
            device_name=str(inviter["inviter_device_name"]),
            root_pub_key=str(inviter["inviter_root_key"]),
            device_pub_key=str(inviter["inviter_device_key"]),
            x25519_pub_key=str(inviter["inviter_x25519_key"]),
            binding_sig=str(inviter["inviter_binding_sig"]),
            endpoint_url=str(inviter.get("endpoint_url") or ""),
            model_label=str(inviter.get("inviter_model") or "local"),
            sas_code=inv.sas_code,
        )
        _append_signed_record(
            db,
            user_id=uid,
            ident=ident,
            dev=dev,
            hill=hill,
            record_type="Membership",
            payload={
                "action": "joined",
                "sas_code": inv.sas_code,
                "admin_root_key": hill.admin_root_key,
            },
        )
        inv.status = "confirmed"
        db.commit()
        return RedirectResponse(
            f"/peers?msg={quote_plus(f'Joined {hill.name} and pinned {peer.display_name} (SAS {inv.sas_code}).')}",
            status_code=303,
        )

    @router.post("/peers/invites/{invite_id}/cancel")
    def cancel_invite(invite_id: int, user: dict[str, Any] = Depends(require_user)):
        db = get_db()
        uid = int(user["sub"])
        inv = (
            db.query(NodeInvite)
            .filter(NodeInvite.id == invite_id, NodeInvite.user_id == uid)
            .first()
        )
        if inv is not None:
            inv.status = "cancelled"
            db.commit()
        return RedirectResponse("/peers?msg=Pairing+cancelled.", status_code=303)

    # -- Peer Removal & Live HTTP Sync -----------------------------------------

    @router.post("/peers/{peer_id}/remove")
    def remove_peer(peer_id: int, user: dict[str, Any] = Depends(require_user)):
        db = get_db()
        uid = int(user["sub"])
        peer = db.query(NodePeer).filter(NodePeer.id == peer_id, NodePeer.user_id == uid).first()
        if peer is None:
            return RedirectResponse("/peers?err=Peer+not+found.", status_code=303)
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == peer.hill_id)
            .first()
        )
        peer.revoked = True
        peer.status = "offline"
        db.commit()
        if hill is not None:
            _sign_membership_update(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                action=f"revoke:{peer.display_name}",
            )
        return RedirectResponse(
            f"/peers?msg={quote_plus(f'Removed {peer.display_name} and signed updated Membership record.')}",
            status_code=303,
        )

    @router.post("/peers/{peer_id}/sync")
    def sync_peer_http(peer_id: int, user: dict[str, Any] = Depends(require_user)):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        peer = (
            db.query(NodePeer)
            .filter(
                NodePeer.id == peer_id,
                NodePeer.user_id == uid,
                NodePeer.verified.is_(True),
                NodePeer.revoked.is_(False),
            )
            .first()
        )
        if peer is None:
            return RedirectResponse("/peers?err=Peer+not+found.", status_code=303)
        if not peer.endpoint_url:
            return RedirectResponse(
                "/peers?tab=log&err=Peer+has+no+direct+HTTP+URL+configured.+Use+encrypted+.anthill-bundle+sync.",
                status_code=303,
            )

        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == peer.hill_id)
            .first()
        )
        if hill is None:
            return RedirectResponse("/peers?err=Hill+not+found.", status_code=303)

        dev_priv = node_id.decrypt_secret(dev.device_priv_key_enc)
        client = node_transport.HttpPeerTransport(
            peer.endpoint_url,
            root_key=ident.root_pub_key,
            device_key=dev.device_pub_key,
            device_priv_b64=dev_priv,
            client_transport=_resolve_transport(peer.endpoint_url),
        )
        local_records = _hill_records_as_dicts(db, uid, hill.hill_id)
        local_heads = node_sync.compute_heads(local_records)
        try:
            remote_heads = client.fetch_heads(hill.hill_id)
            outgoing = node_sync.select_missing_records(local_records, remote_heads)
            outgoing_blobs = _hill_blobs_map(db, uid, outgoing)
            bindings_bundle = _local_device_bindings_bundle(db, uid, ident, hill.hill_id)
            resp = client.exchange_records(
                hill.hill_id, local_heads, outgoing, outgoing_blobs, bindings_bundle
            )
        except node_transport.TransportError as exc:
            peer.status = "offline"
            db.commit()
            return RedirectResponse(f"/peers?err={quote_plus(str(exc))}", status_code=303)

        peer.status = "online"
        peer.last_seen_at = _now_utc()
        summary = _ingest_verified_sync_batch(
            db,
            user_id=uid,
            org_id=org_id,
            hill=hill,
            ident=ident,
            incoming_records=list(resp.get("records") or []),
            incoming_blobs=dict(resp.get("blobs") or {}),
            incoming_bindings=dict(resp.get("device_bindings") or {}),
        )
        _log_egress_usage(
            db,
            user_id=uid,
            org_id=org_id,
            ident=ident,
            dev=dev,
            hill=hill,
            direction="sync",
            peer_name=peer.display_name,
            peer_root_key=peer.root_pub_key,
            model="n/a",
            outcome=f"Sent {len(outgoing)}, received {summary['accepted']} ({summary['proposals_created']} proposals)",
        )
        note = (
            f"Synced with {peer.display_name}: sent {len(outgoing)}, received {summary['accepted']} "
            f"records ({summary['proposals_created']} wiki proposals)."
        )
        return RedirectResponse(f"/peers?msg={quote_plus(note)}", status_code=303)

    # -- Encrypted .anthill-bundle Export / Import & Wiki Publishing -----------

    @router.post("/peers/bundle/export")
    def export_bundle(
        hill_id: str = Form(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill).filter(NodeHill.user_id == uid, NodeHill.hill_id == hill_id).first()
        )
        if hill is None:
            raise HTTPException(status_code=404, detail="Hill not found.")

        peers = (
            db.query(NodePeer)
            .filter(
                NodePeer.user_id == uid,
                NodePeer.hill_id == hill.hill_id,
                NodePeer.verified.is_(True),
                NodePeer.revoked.is_(False),
            )
            .all()
        )
        recipients: dict[str, str] = {ident.root_pub_key: ident.x25519_pub_key}
        for p in peers:
            if p.x25519_pub_key:
                recipients[p.root_pub_key] = p.x25519_pub_key

        records = _hill_records_as_dicts(db, uid, hill.hill_id)
        blobs = _hill_blobs_map(db, uid, records)
        heads = node_sync.compute_heads(records)
        bindings = _local_device_bindings_bundle(db, uid, ident, hill.hill_id)
        dev_priv = node_id.decrypt_secret(dev.device_priv_key_enc)

        bundle_bytes = node_transport.export_encrypted_bundle(
            hill_id=hill.hill_id,
            records=records,
            blobs=blobs,
            heads=heads,
            device_bindings=bindings,
            recipients_x25519=recipients,
            sender_root_key=ident.root_pub_key,
            sender_device_key=dev.device_pub_key,
            sender_device_priv_b64=dev_priv,
            sender_device_name=dev.device_name,
            sender_x25519_key=ident.x25519_pub_key,
            sender_binding_sig=dev.root_binding_sig,
        )
        _log_egress_usage(
            db,
            user_id=uid,
            org_id=org_id,
            ident=ident,
            dev=dev,
            hill=hill,
            direction="bundle_export",
            peer_name=", ".join(p.display_name for p in peers) or "self",
            peer_root_key="",
            model="n/a",
            outcome=f"Encrypted .anthill-bundle ({len(records)} records)",
        )
        filename = f"{slugify(hill.hill_id)}-seq{max(heads.values(), default=0)}.anthill-bundle"
        return Response(
            content=bundle_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/peers/bundle/import")
    async def import_bundle(
        bundle_file: UploadFile = File(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        ident, dev = _ensure_identity(db, uid)
        raw = await bundle_file.read()
        if not raw:
            return RedirectResponse("/peers?tab=log&err=Empty+bundle+file.", status_code=303)

        # Peek hill_id to check allowed members before decryption.
        try:
            outer = json.loads(raw.decode("utf-8"))
            hill_id = str(outer.get("hill_id") or "")
        except Exception:
            return RedirectResponse(
                "/peers?tab=log&err=Invalid+.anthill-bundle+file.", status_code=303
            )

        hill = (
            db.query(NodeHill).filter(NodeHill.user_id == uid, NodeHill.hill_id == hill_id).first()
        )
        if hill is None:
            return RedirectResponse(
                f"/peers?tab=log&err={quote_plus(f'Unknown Hill {hill_id}. Pair with a member first.')}",
                status_code=303,
            )

        active_members, revoked_devs, _ = _active_member_keys(db, uid, hill, ident)
        my_x_priv = node_id.decrypt_secret(ident.x25519_priv_key_enc)
        try:
            decrypted = node_transport.import_encrypted_bundle(
                raw,
                my_root_key=ident.root_pub_key,
                my_x25519_priv_b64=my_x_priv,
                allowed_sender_keys=active_members,
                revoked_devices=revoked_devs,
            )
        except node_transport.TransportError as exc:
            return RedirectResponse(f"/peers?tab=log&err={quote_plus(str(exc))}", status_code=303)

        summary = _ingest_verified_sync_batch(
            db,
            user_id=uid,
            org_id=org_id,
            hill=hill,
            ident=ident,
            incoming_records=list(decrypted.get("records") or []),
            incoming_blobs=dict(decrypted.get("blobs") or {}),
            incoming_bindings=dict(decrypted.get("device_bindings") or {}),
        )
        _log_egress_usage(
            db,
            user_id=uid,
            org_id=org_id,
            ident=ident,
            dev=dev,
            hill=hill,
            direction="bundle_import",
            peer_name=node_id.key_fingerprint(str(decrypted["sender_root_key"])),
            peer_root_key=str(decrypted["sender_root_key"]),
            model="n/a",
            outcome=f"Verified & imported {summary['accepted']} records ({summary['proposals_created']} proposals)",
        )
        note = (
            f"Verified signatures & imported {summary['accepted']} records "
            f"({summary['proposals_created']} proposed to Knowledge Review Gate)."
        )
        return RedirectResponse(f"/peers?tab=log&msg={quote_plus(note)}", status_code=303)

    @router.post("/peers/wiki/publish")
    def publish_wiki_page_to_hill(
        hill_id: str = Form(...),
        slug: str = Form(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill).filter(NodeHill.user_id == uid, NodeHill.hill_id == hill_id).first()
        )
        if hill is None:
            return RedirectResponse("/peers?tab=log&err=Hill+not+found.", status_code=303)

        ws = workspace_for("personal", user_id=uid, org_id=org_id)
        page_path = ws.wiki / f"{slugify(slug)}.md"
        if not page_path.is_file():
            return RedirectResponse("/peers?tab=log&err=Wiki+page+not+found.", status_code=303)
        content = page_path.read_text(encoding="utf-8")
        title = first_h1(content) or slug
        _append_signed_record(
            db,
            user_id=uid,
            ident=ident,
            dev=dev,
            hill=hill,
            record_type="PageChange",
            payload={"slug": slugify(slug), "title": title, "excerpt": content[:280]},
            content_body=content,
        )
        return RedirectResponse(
            f"/peers?tab=log&msg={quote_plus(f'Signed PageChange for {title} added to log (detached body).')}",
            status_code=303,
        )

    @router.post("/peers/records/{record_id}/erase")
    def erase_detached_blob(record_id: int, user: dict[str, Any] = Depends(require_user)):
        """Erase detached content body and sign a Deletion record without breaking chain hashes."""
        db = get_db()
        uid = int(user["sub"])
        rec = (
            db.query(NodeRecord)
            .filter(NodeRecord.id == record_id, NodeRecord.user_id == uid)
            .first()
        )
        if rec is None or not rec.content_hash:
            return RedirectResponse(
                "/peers?tab=log&err=No+detached+content+blob+for+this+record.", status_code=303
            )
        blob = (
            db.query(NodeBlob)
            .filter(NodeBlob.user_id == uid, NodeBlob.content_hash == rec.content_hash)
            .first()
        )
        if blob is not None:
            blob.content_text = None
            blob.erased_at = _now_utc()
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == rec.hill_id)
            .first()
        )
        if hill is not None:
            _append_signed_record(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                record_type="Deletion",
                payload={
                    "erased_record_hash": rec.record_hash,
                    "erased_content_ref": rec.content_ref,
                },
            )
        db.commit()
        return RedirectResponse(
            "/peers?tab=log&msg=Detached+page+body+erased+and+Deletion+record+signed+(chain+intact).",
            status_code=303,
        )

    # -- Knowledge Lending Matrix ----------------------------------------------

    @router.post("/peers/lending/toggle")
    def toggle_lending(
        hill_id: str = Form(...),
        area_id: str = Form(...),
        area_name: str = Form(""),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        if area_id == node_lending.LOCKED_AREA_ID:
            return RedirectResponse(
                "/peers?tab=lending&err=Personal+%2F+Sensitive+area+is+locked+and+cannot+be+lent.",
                status_code=303,
            )
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill).filter(NodeHill.user_id == uid, NodeHill.hill_id == hill_id).first()
        )
        if hill is None:
            return RedirectResponse("/peers?tab=lending&err=Hill+not+found.", status_code=303)

        rule = (
            db.query(NodeLendingRule)
            .filter(
                NodeLendingRule.user_id == uid,
                NodeLendingRule.hill_id == hill_id,
                NodeLendingRule.area_id == area_id,
            )
            .first()
        )
        if rule is None:
            current = node_lending.default_allowed_for_hill(area_id, hill.scope, False)
            rule = NodeLendingRule(
                user_id=uid,
                hill_id=hill_id,
                area_id=area_id,
                area_name=area_name or area_id,
                allowed=not current,
                locked=False,
            )
            db.add(rule)
        elif rule.locked:
            return RedirectResponse(
                "/peers?tab=lending&err=This+area+is+locked+as+never+lent.", status_code=303
            )
        else:
            rule.allowed = not rule.allowed
        db.commit()

        _append_signed_record(
            db,
            user_id=uid,
            ident=ident,
            dev=dev,
            hill=hill,
            record_type="Lending",
            payload={
                "area_id": area_id,
                "area_name": rule.area_name,
                "allowed": bool(rule.allowed),
            },
        )
        verb = "Now lending" if rule.allowed else "Stopped lending"
        return RedirectResponse(
            f"/peers?tab=lending&msg={quote_plus(f'{verb} {rule.area_name} to {hill.name}.')}",
            status_code=303,
        )

    # -- Identity, Recovery, Device Revocation & Open Export -------------------

    @router.post("/peers/identity/recovery-test")
    def test_recovery_kit(
        phrase: str = Form(""),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        ident, _ = _ensure_identity(db, uid)
        if not phrase.strip():
            # Built-in round-trip self-test of the stored root key's recovery encoding.
            kit = node_id.export_recovery_kit(node_id.decrypt_secret(ident.root_priv_key_enc))
            ok = node_id.verify_recovery_kit(kit, ident.root_pub_key)
        else:
            ok = node_id.verify_recovery_kit(phrase, ident.root_pub_key)
        if not ok:
            return RedirectResponse(
                "/peers?tab=identity&err=Recovery+kit+did+not+match+your+Ed25519+root+key.",
                status_code=303,
            )
        ident.recovery_verified_at = _now_utc()
        db.commit()
        return RedirectResponse(
            "/peers?tab=identity&msg=33-word+recovery+kit+verified+cleanly+against+your+Ed25519+root+key.",
            status_code=303,
        )

    @router.post("/peers/devices/{device_id}/revoke")
    def revoke_device(device_id: int, user: dict[str, Any] = Depends(require_user)):
        db = get_db()
        uid = int(user["sub"])
        dev = (
            db.query(NodeDevice)
            .filter(NodeDevice.id == device_id, NodeDevice.user_id == uid)
            .first()
        )
        if dev is None:
            return RedirectResponse("/peers?tab=identity&err=Device+not+found.", status_code=303)
        if dev.is_current:
            return RedirectResponse(
                "/peers?tab=identity&err=Cannot+revoke+the+currently+active+device+key+from+itself.",
                status_code=303,
            )
        ident, cur_dev = _ensure_identity(db, uid)
        now_iso = _now_utc().isoformat()
        root_priv = node_id.decrypt_secret(ident.root_priv_key_enc)
        dev.revoked_at = _now_utc()
        dev.revocation_sig = node_id.sign_device_revocation(
            root_priv, ident.root_pub_key, dev.device_pub_key, now_iso
        )
        db.commit()
        for hill in db.query(NodeHill).filter(NodeHill.user_id == uid).all():
            if hill.admin_root_key == ident.root_pub_key:
                _sign_membership_update(
                    db,
                    user_id=uid,
                    ident=ident,
                    dev=cur_dev,
                    hill=hill,
                    action=f"revoke-device:{dev.device_name}",
                )
        return RedirectResponse(
            f"/peers?tab=identity&msg={quote_plus(f'Revoked device key for {dev.device_name}.')}",
            status_code=303,
        )

    @router.get("/peers/export-all")
    def export_all_open_format(user: dict[str, Any] = Depends(require_user)):
        """Rule 7: Export OKGF wikis + signed JSONL record log + public identity (no private keys)."""
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        ident, _ = _ensure_identity(db, uid)
        records = [
            json.loads(r.envelope_json)
            for r in db.query(NodeRecord)
            .filter(NodeRecord.user_id == uid)
            .order_by(NodeRecord.id.asc())
            .all()
        ]
        hills = [
            {
                "hill_id": h.hill_id,
                "name": h.name,
                "scope": h.scope,
                "admin_root_key": h.admin_root_key,
            }
            for h in db.query(NodeHill).filter(NodeHill.user_id == uid).all()
        ]
        devices = [
            {
                "device_name": d.device_name,
                "device_pub_key": d.device_pub_key,
                "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None,
            }
            for d in db.query(NodeDevice).filter(NodeDevice.user_id == uid).all()
        ]
        workspaces_map = {
            f"{scope}-{label or 'default'}": ws
            for scope, ws, label in _user_workspaces(db, uid, org_id)
        }
        tgz = node_id.build_open_export_tgz(
            workspaces_map,
            records,
            {
                "root_pub_key": ident.root_pub_key,
                "x25519_pub_key": ident.x25519_pub_key,
                "devices": devices,
                "hills": hills,
            },
        )
        return Response(
            content=tgz,
            media_type="application/gzip",
            headers={"Content-Disposition": 'attachment; filename="anthill-open-export.tar.gz"'},
        )

    # -- Counter-Check Consultation & Open Thread (/peers/consult) -------------

    @router.get("/peers/consult", response_class=HTMLResponse)
    def consult_page(
        request: Request,
        cid: int | None = None,
        sort: str = "new",
        preview: int = 0,
        msg: str = "",
        err: str = "",
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        if not settings.enabled:
            return RedirectResponse(
                "/peers?err=Enable+Node+peer-to-peer+to+consult+peers.", status_code=303
            )

        ident, dev = _ensure_identity(db, uid)
        default_hill = _ensure_default_hill(db, uid, ident, dev)
        hills = db.query(NodeHill).filter(NodeHill.user_id == uid).all()
        consults = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.user_id == uid)
            .order_by(NodeConsultation.updated_at.desc())
            .limit(20)
            .all()
        )
        active = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
            if cid
            else (consults[0] if consults else None)
        )
        hill_id = active.hill_id if active else default_hill.hill_id
        peers = (
            db.query(NodePeer)
            .filter(
                NodePeer.user_id == uid,
                NodePeer.hill_id == hill_id,
                NodePeer.verified.is_(True),
                NodePeer.revoked.is_(False),
            )
            .all()
        )

        suggestions: list[dict[str, Any]] = json.loads(active.suggestions_json) if active else []
        if active and active.mode == "open":
            if sort == "disagree":
                suggestions = sorted(
                    suggestions,
                    key=lambda s: (
                        0 if s.get("kind") == "disagree" else 1,
                        -int(s.get("votes", 0)),
                    ),
                )
            elif sort == "top":
                suggestions = sorted(suggestions, key=lambda s: -int(s.get("votes", 0)))

        own_answers: list[dict[str, Any]] = json.loads(active.own_answers_json) if active else []
        brief_preview = (
            node_consult.build_egress_brief(active.question, active.draft_base) if active else None
        )
        _, local_model = _local_model_info(db, org_id)

        return templates.TemplateResponse(
            request,
            "consult.html",
            {
                "request": request,
                "user": user,
                "hills": hills,
                "default_hill": default_hill,
                "consults": consults,
                "active": active,
                "peers": peers,
                "suggestions": suggestions,
                "own_answers": own_answers,
                "brief_preview": brief_preview,
                "show_preview": bool(preview),
                "sort": sort,
                "local_model": local_model,
                "msg": msg,
                "err": err,
            },
        )

    @router.post("/peers/consult/start")
    def start_consultation(
        question: str = Form(...),
        draft: str = Form(""),
        hill_id: str = Form(""),
        conversation_id: int | None = Form(None),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        settings = _ensure_settings(db, uid)
        if not settings.enabled:
            raise HTTPException(status_code=403, detail="Enable Node peer-to-peer first.")
        ident, dev = _ensure_identity(db, uid)
        default_hill = _ensure_default_hill(db, uid, ident, dev)
        chosen_hill = hill_id.strip() or default_hill.hill_id
        _, local_model = _local_model_info(db, org_id)

        q_text = question.strip()
        d_text = draft.strip()
        if not q_text:
            return RedirectResponse("/peers/consult?err=Question+is+required.", status_code=303)
        if not d_text:
            # Draft an answer locally if the user didn't supply one (never leaves machine yet).
            try:
                backend = _build_local_backend(db, org_id, uid)
                from ..inference.base import Message

                d_text = str(
                    backend.chat(
                        [
                            Message(
                                "system", "Give a concise local draft answer in at most 90 words."
                            ),
                            Message("user", q_text),
                        ]
                    )
                    or ""
                ).strip()
            except Exception:
                d_text = f"Local draft for: {q_text}"

        c = NodeConsultation(
            user_id=uid,
            hill_id=chosen_hill,
            conversation_id=conversation_id,
            question=q_text,
            draft_base=d_text,
            draft_current=d_text,
            model=local_model,
            mode="solo",
            suggestions_json="[]",
            own_answers_json="[]",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return RedirectResponse(f"/peers/consult?cid={c.id}&preview=1", status_code=303)

    @router.post("/peers/consult/{cid}/send")
    def send_consultation(
        cid: int,
        confirm_egress: str = Form(""),
        peer_ids: list[int] = Form(default=[]),
        user: dict[str, Any] = Depends(require_user),
    ):
        """Rule 2: Requires explicit egress confirmation after visible exit preview."""
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        if confirm_egress != "yes":
            return RedirectResponse(
                f"/peers/consult?cid={cid}&preview=1&err=Review+and+confirm+the+visible+exit+preview+before+sending.",
                status_code=303,
            )
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
            .first()
        )
        if hill is None:
            raise HTTPException(status_code=404, detail="Hill not found.")

        brief = node_consult.build_egress_brief(c.question, c.draft_base)
        # Always write signed Question record (supports offline consult through the log too!).
        _append_signed_record(
            db,
            user_id=uid,
            ident=ident,
            dev=dev,
            hill=hill,
            record_type="Question",
            model=c.model,
            payload={
                "consultation_id": c.id,
                "question": brief["question"],
                "draft_brief": brief["draft"],
            },
        )

        peers_q = db.query(NodePeer).filter(
            NodePeer.user_id == uid,
            NodePeer.hill_id == hill.hill_id,
            NodePeer.verified.is_(True),
            NodePeer.revoked.is_(False),
        )
        if peer_ids:
            peers_q = peers_q.filter(NodePeer.id.in_(peer_ids))
        targets = peers_q.all()

        existing_sugs: list[dict[str, Any]] = json.loads(c.suggestions_json or "[]")
        dev_priv = node_id.decrypt_secret(dev.device_priv_key_enc)
        consulted_names: list[str] = []
        new_count = 0

        for peer in targets:
            if not peer.endpoint_url:
                continue
            client = node_transport.HttpPeerTransport(
                peer.endpoint_url,
                root_key=ident.root_pub_key,
                device_key=dev.device_pub_key,
                device_priv_b64=dev_priv,
                client_transport=_resolve_transport(peer.endpoint_url),
            )
            try:
                reply = client.consult(hill.hill_id, brief["question"], brief["draft"])
            except node_transport.TransportError:
                peer.status = "offline"
                continue
            peer.status = "online"
            peer.last_seen_at = _now_utc()
            consulted_names.append(peer.display_name)
            peer_model = str(reply.get("model") or peer.model_label or "local")
            for item in reply.get("suggestions") or []:
                sug_id = f"sug-{secrets.token_hex(4)}"
                sug_entry = {
                    "id": sug_id,
                    "peer_id": peer.id,
                    "peer_root_key": peer.root_pub_key,
                    "author": f"{peer.display_name}'s AI",
                    "human_author": f"{peer.display_name} with {peer_model}",
                    "model": peer_model,
                    "karma": int(peer.karma),
                    "kind": str(item.get("kind") or "wording"),
                    "issue": str(item.get("issue") or "Counter-check note"),
                    "wording": str(item.get("wording") or ""),
                    "reason": str(item.get("reason") or ""),
                    "sources": list(item.get("sources") or []),
                    "status": "pending",
                    "votes": 0,
                }
                existing_sugs.append(sug_entry)
                new_count += 1

            _log_egress_usage(
                db,
                user_id=uid,
                org_id=org_id,
                ident=ident,
                dev=dev,
                hill=hill,
                direction="outbound_brief",
                peer_name=peer.display_name,
                peer_root_key=peer.root_pub_key,
                model=peer_model,
                outcome=f"Received {len(reply.get('suggestions') or [])} counter-check suggestion(s)",
                count_as_peer_metric=True,
            )

        if not consulted_names:
            # Offline consult recorded through the log (will sync via .anthill-bundle).
            _log_egress_usage(
                db,
                user_id=uid,
                org_id=org_id,
                ident=ident,
                dev=dev,
                hill=hill,
                direction="outbound_brief",
                peer_name="Hill log (offline)",
                peer_root_key="",
                model=c.model,
                outcome="Queued Question record in signed log for offline bundle sync",
                count_as_peer_metric=True,
            )

        c.suggestions_json = json.dumps(existing_sugs)
        c.updated_at = _now_utc()
        db.commit()

        note = (
            f"Consulted {', '.join(consulted_names)}: received {new_count} structured suggestion(s)."
            if consulted_names
            else "Peers offline: Question + draft brief signed into record log for offline bundle sync."
        )
        return RedirectResponse(
            f"/peers/consult?cid={c.id}&msg={quote_plus(note)}", status_code=303
        )

    @router.post("/peers/consult/{cid}/decide")
    def decide_suggestion(
        cid: int,
        suggestion_id: str = Form(...),
        decision: str = Form(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        if decision not in ("accepted", "rejected"):
            raise HTTPException(status_code=400, detail="Decision must be accepted or rejected.")
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        sugs: list[dict[str, Any]] = json.loads(c.suggestions_json or "[]")
        target = next((s for s in sugs if s.get("id") == suggestion_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Suggestion not found.")

        prev_status = target.get("status")
        target["status"] = decision
        if decision == "accepted" and prev_status != "accepted":
            target["karma"] = int(target.get("karma", 0)) + 4
            if target.get("peer_id"):
                peer = (
                    db.query(NodePeer)
                    .filter(NodePeer.id == int(target["peer_id"]), NodePeer.user_id == uid)
                    .first()
                )
                if peer is not None:
                    peer.karma = int(peer.karma) + 4
            ident, dev = _ensure_identity(db, uid)
            hill = (
                db.query(NodeHill)
                .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
                .first()
            )
            if hill is not None:
                _append_signed_record(
                    db,
                    user_id=uid,
                    ident=ident,
                    dev=dev,
                    hill=hill,
                    record_type="Acceptance",
                    model=str(target.get("model") or ""),
                    payload={
                        "consultation_id": c.id,
                        "suggestion_id": suggestion_id,
                        "peer_root_key": str(target.get("peer_root_key") or ""),
                        "decision": "accepted",
                    },
                )

        c.suggestions_json = json.dumps(sugs)
        c.draft_current = node_consult.apply_accepted_suggestions(c.draft_base, sugs)
        c.updated_at = _now_utc()
        db.commit()
        note = (
            "Accepted suggestion, updated draft, signed Acceptance record, and awarded +4 karma."
            if decision == "accepted"
            else "Rejected suggestion (kept visible per counter-check protocol)."
        )
        return RedirectResponse(
            f"/peers/consult?cid={c.id}&msg={quote_plus(note)}", status_code=303
        )

    @router.post("/peers/consult/{cid}/open-thread")
    def open_to_people(cid: int, user: dict[str, Any] = Depends(require_user)):
        """Upgrade consultation to an Open Thread (Rule 11: humans post only, no system synthesis)."""
        db = get_db()
        uid = int(user["sub"])
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        c.mode = "open" if c.mode == "solo" else "solo"
        c.updated_at = _now_utc()
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
            .first()
        )
        if hill is not None:
            _append_signed_record(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                record_type="ThreadSettings",
                payload={"consultation_id": c.id, "mode": c.mode, "humans_post_only": True},
            )
        db.commit()
        note = (
            "Opened thread to people (Rule 11: humans post only, no system-made synthesis)."
            if c.mode == "open"
            else "Switched back to solo consultation."
        )
        return RedirectResponse(
            f"/peers/consult?cid={c.id}&msg={quote_plus(note)}", status_code=303
        )

    @router.post("/peers/consult/{cid}/post")
    def post_human_thread_entry(
        cid: int,
        kind: str = Form("own_answer"),
        body: str = Form(...),
        user: dict[str, Any] = Depends(require_user),
    ):
        """Rule 11: Humans post own answers or comments in an Open Thread."""
        db = get_db()
        uid = int(user["sub"])
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None or c.mode != "open":
            return RedirectResponse(
                f"/peers/consult?cid={cid}&err=Open+the+thread+to+people+first.", status_code=303
            )
        text = body.strip()
        if not text:
            return RedirectResponse(
                f"/peers/consult?cid={cid}&err=Post+text+is+empty.", status_code=303
            )

        me = db.query(User).filter(User.id == uid).first()
        author = (me.display_name or me.email) if me else f"User {uid}"
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
            .first()
        )
        if kind == "own_answer":
            own = json.loads(c.own_answers_json or "[]")
            entry = {
                "id": f"own-{secrets.token_hex(4)}",
                "author": author,
                "model": "human-posted",
                "text": text,
                "votes": 0,
            }
            own.append(entry)
            c.own_answers_json = json.dumps(own)
            rec_type = "Answer"
        else:
            sugs = json.loads(c.suggestions_json or "[]")
            sugs.append(
                {
                    "id": f"hum-{secrets.token_hex(4)}",
                    "author": author,
                    "human_author": author,
                    "model": "human-posted",
                    "karma": 0,
                    "kind": "wording",
                    "issue": "Human comment",
                    "wording": text,
                    "reason": "Posted directly by human member",
                    "sources": [],
                    "status": "pending",
                    "votes": 0,
                }
            )
            c.suggestions_json = json.dumps(sugs)
            rec_type = "SuggestedChange"

        if hill is not None:
            _append_signed_record(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                record_type=rec_type,
                payload={"consultation_id": c.id, "author": author, "text": text[:500]},
            )
        c.updated_at = _now_utc()
        db.commit()
        return RedirectResponse(
            f"/peers/consult?cid={c.id}&msg=Posted+signed+entry+to+thread.", status_code=303
        )

    @router.post("/peers/consult/{cid}/vote")
    def vote_thread_entry(
        cid: int,
        item_id: str = Form(...),
        bucket: str = Form("suggestion"),
        user: dict[str, Any] = Depends(require_user),
    ):
        db = get_db()
        uid = int(user["sub"])
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        if bucket == "own":
            items = json.loads(c.own_answers_json or "[]")
            for it in items:
                if it.get("id") == item_id:
                    it["votes"] = int(it.get("votes", 0)) + 1
            c.own_answers_json = json.dumps(items)
        else:
            items = json.loads(c.suggestions_json or "[]")
            for it in items:
                if it.get("id") == item_id:
                    it["votes"] = int(it.get("votes", 0)) + 1
            c.suggestions_json = json.dumps(items)
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
            .first()
        )
        if hill is not None:
            _append_signed_record(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                record_type="Vote",
                payload={"consultation_id": c.id, "item_id": item_id, "value": 1},
            )
        db.commit()
        return RedirectResponse(f"/peers/consult?cid={c.id}", status_code=303)

    @router.post("/peers/consult/{cid}/propose-wiki")
    def propose_consult_to_wiki(cid: int, user: dict[str, Any] = Depends(require_user)):
        """Rule 4: Propose consultation outcome to Knowledge Review Gate (never writes directly)."""
        db = get_db()
        uid = int(user["sub"])
        org_id = int(user["org"])
        c = (
            db.query(NodeConsultation)
            .filter(NodeConsultation.id == cid, NodeConsultation.user_id == uid)
            .first()
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        ident, dev = _ensure_identity(db, uid)
        hill = (
            db.query(NodeHill)
            .filter(NodeHill.user_id == uid, NodeHill.hill_id == c.hill_id)
            .first()
        )
        title = c.question.strip().rstrip("?")[:90]
        slug = slugify(title)
        body = f"# {title}\n\n{c.draft_current}\n"
        rec_hash = ""
        if hill is not None:
            env = _append_signed_record(
                db,
                user_id=uid,
                ident=ident,
                dev=dev,
                hill=hill,
                record_type="PageChange",
                model=c.model,
                payload={"slug": slug, "title": title, "from_consultation": c.id},
                content_body=body,
            )
            rec_hash = env["record_hash"]

        me = db.query(User).filter(User.id == uid).first()
        signer_name = (me.display_name or me.email) if me else f"User {uid}"
        db.add(
            WikiReview(
                org_id=org_id,
                proposed_by=uid,
                provenance_hash=rec_hash[:64],
                slug=slug,
                kind="page",
                content=body,
                diff_summary=f"Proposed from peer consultation #{c.id}",
                target_scope="personal",
                outline="Proposed from peer counter-check consultation. Review and approve to save into your local wiki.",
                flags=json.dumps(["peer_proposed"]),
                status="pending",
                peer_record_hash=rec_hash,
                peer_state="proposed",
                peer_hill_id=c.hill_id,
                peer_signer_key=ident.root_pub_key,
                peer_signer_name=signer_name,
            )
        )
        db.commit()
        return RedirectResponse("/wiki/review/personal", status_code=303)

    # -- Authenticated Peer-to-Peer HTTP Endpoints (/api/node/p2p/*) -----------

    @router.post("/api/node/p2p/pair/request")
    async def p2p_pair_request(request: Request):
        """Receive a joiner's signed handshake for an active pending invite on this desktop."""
        db = get_db()
        owner, _settings, ident, _dev = _resolve_active_node_owner(db)
        data = await request.json()
        nonce = str(data.get("nonce") or "")
        inv = (
            db.query(NodeInvite)
            .filter(
                NodeInvite.user_id == owner.id,
                NodeInvite.nonce == nonce,
                NodeInvite.role == "inviter",
                NodeInvite.status == "pending",
            )
            .first()
        )
        if inv is None:
            raise HTTPException(status_code=404, detail="No active invite found for this nonce.")
        exp = inv.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if _now_utc() > exp:
            inv.status = "expired"
            db.commit()
            raise HTTPException(status_code=410, detail="Pairing invite has expired.")
        try:
            verified = node_pairing.verify_join_response(data, expected_nonce=nonce)
        except node_pairing.PairingError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        inv.sas_code = node_pairing.compute_sas_code(
            ident.root_pub_key, str(verified["joiner_root_key"]), nonce
        )
        inv.peer_payload_json = json.dumps({"joiner": verified})
        inv.status = "awaiting_sas"
        db.commit()
        return JSONResponse({"ok": True, "sas_code": inv.sas_code, "status": "awaiting_sas"})

    @router.post("/api/node/p2p/ping")
    async def p2p_ping(request: Request):
        db = get_db()
        owner, settings, _ident, _dev = _resolve_active_node_owner(db)
        raw = await request.body()
        data = json.loads(raw.decode("utf-8") or "{}")
        hill_id = str(data.get("hill_id") or "")
        _hill, _peer = _authenticate_peer_request(
            db,
            owner_user_id=int(owner.id),
            hill_id=hill_id,
            method="POST",
            path=request.url.path,
            body_bytes=raw,
            headers=request.headers,
        )
        if not settings.presence_visible:
            return JSONResponse({"online": False, "presence": "hidden"})
        return JSONResponse({"online": True, "presence": "visible"})

    @router.post("/api/node/p2p/heads")
    async def p2p_heads(request: Request):
        db = get_db()
        owner, _settings, _ident, _dev = _resolve_active_node_owner(db)
        raw = await request.body()
        data = json.loads(raw.decode("utf-8") or "{}")
        hill_id = str(data.get("hill_id") or "")
        hill, _peer = _authenticate_peer_request(
            db,
            owner_user_id=int(owner.id),
            hill_id=hill_id,
            method="POST",
            path=request.url.path,
            body_bytes=raw,
            headers=request.headers,
        )
        recs = _hill_records_as_dicts(db, int(owner.id), hill.hill_id)
        return JSONResponse({"hill_id": hill.hill_id, "heads": node_sync.compute_heads(recs)})

    @router.post("/api/node/p2p/sync")
    async def p2p_sync(request: Request):
        db = get_db()
        owner, _settings, ident, dev = _resolve_active_node_owner(db)
        raw = await request.body()
        data = json.loads(raw.decode("utf-8") or "{}")
        hill_id = str(data.get("hill_id") or "")
        hill, peer = _authenticate_peer_request(
            db,
            owner_user_id=int(owner.id),
            hill_id=hill_id,
            method="POST",
            path=request.url.path,
            body_bytes=raw,
            headers=request.headers,
        )
        summary = _ingest_verified_sync_batch(
            db,
            user_id=int(owner.id),
            org_id=int(owner.org_id),
            hill=hill,
            ident=ident,
            incoming_records=list(data.get("records") or []),
            incoming_blobs=dict(data.get("blobs") or {}),
            incoming_bindings=dict(data.get("device_bindings") or {}),
        )
        local_records = _hill_records_as_dicts(db, int(owner.id), hill.hill_id)
        remote_heads = {str(k): int(v) for k, v in (data.get("heads") or {}).items()}
        missing_for_peer = node_sync.select_missing_records(local_records, remote_heads)
        missing_blobs = _hill_blobs_map(db, int(owner.id), missing_for_peer)
        bindings = _local_device_bindings_bundle(db, int(owner.id), ident, hill.hill_id)
        _log_egress_usage(
            db,
            user_id=int(owner.id),
            org_id=int(owner.org_id),
            ident=ident,
            dev=dev,
            hill=hill,
            direction="sync",
            peer_name=peer.display_name,
            peer_root_key=peer.root_pub_key,
            model="n/a",
            outcome=f"Inbound sync: accepted {summary['accepted']}, returned {len(missing_for_peer)}",
        )
        return JSONResponse(
            {
                "hill_id": hill.hill_id,
                "heads": node_sync.compute_heads(local_records),
                "records": missing_for_peer,
                "blobs": missing_blobs,
                "device_bindings": bindings,
                "accepted": summary["accepted"],
                "rejected": summary["rejected"],
            }
        )

    @router.post("/api/node/p2p/consult")
    async def p2p_consult(request: Request):
        """Rule 3 & 13: 0-tool isolated peer consultation over lent areas only, capped & scrubbed."""
        db = get_db()
        owner, _settings, ident, dev = _resolve_active_node_owner(db)
        raw = await request.body()
        data = json.loads(raw.decode("utf-8") or "{}")
        hill_id = str(data.get("hill_id") or "")
        hill, peer = _authenticate_peer_request(
            db,
            owner_user_id=int(owner.id),
            hill_id=hill_id,
            method="POST",
            path=request.url.path,
            body_bytes=raw,
            headers=request.headers,
        )
        allowed, _remaining = _peer_rate_limiter.allow(peer.root_pub_key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Per-peer consultation quota exceeded ({PEER_RATE_LIMIT_PER_HOUR} req/hr).",
            )

        question = str(data.get("question") or "").strip()[:2000]
        draft = str(data.get("draft") or "").strip()[:3000]
        if not question:
            raise HTTPException(status_code=400, detail="Consultation question is required.")

        # Physical filtering in Python outside the model (Rule 3).
        workspaces = _user_workspaces(db, int(owner.id), int(owner.org_id))
        areas = node_lending.discover_wiki_areas(workspaces)
        allowed_areas = _allowed_lending_areas(db, int(owner.id), hill, areas)
        locked_areas = {a.area_id for a in areas if a.locked} | {node_lending.LOCKED_AREA_ID}
        lent_excerpts = node_lending.select_lent_excerpts(
            workspaces, allowed_areas=allowed_areas, locked_areas=locked_areas
        )

        _, local_model = _local_model_info(db, int(owner.org_id))
        backend = _build_local_backend(db, int(owner.org_id), int(owner.id))
        result = node_consult.peer_counter_check(
            backend,
            question=question,
            draft=draft,
            lent_sources=lent_excerpts,
            model_label=local_model,
            max_tokens=MAX_CONSULT_TOKENS,
        )

        _log_egress_usage(
            db,
            user_id=int(owner.id),
            org_id=int(owner.org_id),
            ident=ident,
            dev=dev,
            hill=hill,
            direction="inbound_consult",
            peer_name=peer.display_name,
            peer_root_key=peer.root_pub_key,
            model=str(result["model"]),
            outcome=(
                f"Answered from {len(result['lent_areas_used'])} lent area(s) (0 tools)"
                if result["lent_areas_used"]
                else "Answered from model weights (0 lent areas matched, 0 tools)"
            ),
        )
        return JSONResponse(result)

    return router


def _upsert_verified_peer(
    db: Session,
    *,
    user_id: int,
    hill_id: str,
    display_name: str,
    device_name: str,
    root_pub_key: str,
    device_pub_key: str,
    x25519_pub_key: str,
    binding_sig: str,
    endpoint_url: str,
    model_label: str,
    sas_code: str,
) -> NodePeer:
    peer = (
        db.query(NodePeer)
        .filter(
            NodePeer.user_id == user_id,
            NodePeer.hill_id == hill_id,
            NodePeer.root_pub_key == root_pub_key,
        )
        .first()
    )
    if peer is None:
        peer = NodePeer(
            user_id=user_id,
            hill_id=hill_id,
            display_name=display_name,
            device_name=device_name,
            root_pub_key=root_pub_key,
            device_pub_key=device_pub_key,
            x25519_pub_key=x25519_pub_key,
            binding_sig=binding_sig,
            endpoint_url=endpoint_url,
            model_label=model_label,
            verified=True,
            sas_code=sas_code,
            trust_label=f"Direct trust (SAS {sas_code})",
            status="online" if endpoint_url else "offline",
            revoked=False,
        )
        db.add(peer)
    else:
        peer.display_name = display_name
        peer.device_name = device_name
        peer.device_pub_key = device_pub_key
        peer.x25519_pub_key = x25519_pub_key
        peer.binding_sig = binding_sig
        peer.endpoint_url = endpoint_url
        peer.model_label = model_label
        peer.verified = True
        peer.sas_code = sas_code
        peer.trust_label = f"Direct trust (SAS {sas_code})"
        peer.revoked = False
        peer.status = "online" if endpoint_url else "offline"
    db.commit()
    db.refresh(peer)
    return peer


def _record_summary(env: dict[str, Any]) -> str:
    rtype = str(env.get("record_type") or "")
    p = env.get("payload") or {}
    if rtype == "Membership":
        return f"Membership update ({p.get('action', 'update')}) - {len(p.get('members') or [])} member(s)"
    if rtype == "PageChange":
        return f"PageChange: {p.get('title') or p.get('slug', 'page')}"
    if rtype == "Acceptance":
        return f"Accepted {p.get('slug') or p.get('suggestion_id') or 'change'}"
    if rtype == "Deletion":
        return f"Erased detached body ({str(p.get('erased_record_hash') or '')[:16]})"
    if rtype == "Lending":
        verb = "Lent" if p.get("allowed") else "Stopped lending"
        return f"{verb} area: {p.get('area_name') or p.get('area_id', '')}"
    if rtype == "Question":
        return f"Question brief logged (consult #{p.get('consultation_id', '')})"
    if rtype == "ThreadSettings":
        return f"Thread mode: {p.get('mode', 'solo')}"
    if rtype == "Vote":
        return f"Vote on {p.get('item_id', 'post')}"
    if rtype == "Usage":
        return f"Usage ({p.get('direction', 'peer')}): {p.get('outcome', '')}"
    if rtype in ("Answer", "SuggestedChange"):
        return f"{rtype} by {p.get('author', 'member')}"
    return rtype
