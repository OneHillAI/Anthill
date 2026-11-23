"""Transport-agnostic sync interface + two working pilot transports (Sec 4.1 #5).

1. Encrypted file bundle (``.anthill-bundle``): H2 offline-first transport encrypted to group
   members' X25519 keys (ephemeral ECDH + HKDF-SHA256 + AES-256-GCM) and Ed25519 signed.
2. Direct LAN / Peer HTTP transport: Ed25519 signed HTTP request headers verified against
   pinned peer keys, loopback-only by default with opt-in endpoint URLs for LAN/Tailscale.

``iroh`` or Tor transports can implement ``NodeTransport`` later without touching sync logic.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .identity import (
    b64u_decode,
    b64u_encode,
    ed25519_sign,
    ed25519_verify,
    load_x25519_private,
    load_x25519_public,
    verify_device_binding,
)
from .log import canonical_json

BUNDLE_FORMAT = "anthill-bundle"
BUNDLE_VERSION = 1


class TransportError(Exception):
    """Failed bundle crypto, signature check, or peer HTTP call."""


class NodeTransport(Protocol):
    """Transport-agnostic interface for head comparison and delta exchange."""

    name: str

    def fetch_heads(self, hill_id: str) -> dict[str, int]: ...

    def exchange_records(
        self,
        hill_id: str,
        local_heads: dict[str, int],
        outgoing_records: list[dict[str, Any]],
        outgoing_blobs: dict[str, str],
        device_bindings: dict[str, dict[str, str]],
    ) -> dict[str, Any]: ...


# -- Transport 1: Encrypted .anthill-bundle (offline-first) --------------------


def _derive_wrap_key(shared_secret: bytes, hill_id: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"anthill-bundle-v1:{hill_id}".encode(),
    ).derive(shared_secret)


def export_encrypted_bundle(
    *,
    hill_id: str,
    records: list[dict[str, Any]],
    blobs: dict[str, str],
    heads: dict[str, int],
    device_bindings: dict[str, dict[str, str]],
    recipients_x25519: dict[str, str],
    sender_root_key: str,
    sender_device_key: str,
    sender_device_priv_b64: str,
    sender_device_name: str,
    sender_x25519_key: str,
    sender_binding_sig: str,
) -> bytes:
    """Encrypt records + detached blobs to all recipient members' X25519 keys and sign the bundle."""
    if not recipients_x25519:
        raise TransportError("At least one recipient X25519 key is required to encrypt a bundle.")

    plaintext = canonical_json(
        {
            "hill_id": hill_id,
            "heads": heads,
            "records": records,
            "blobs": blobs,
            "device_bindings": device_bindings,
        }
    ).encode("utf-8")

    cek = secrets.token_bytes(32)
    payload_nonce = secrets.token_bytes(12)
    aad = f"{BUNDLE_FORMAT}:{BUNDLE_VERSION}:{hill_id}".encode()
    ciphertext = AESGCM(cek).encrypt(payload_nonce, plaintext, aad)

    recipient_slots: list[dict[str, str]] = []
    for root_key, x25519_pub_b64 in sorted(recipients_x25519.items()):
        eph_priv = X25519PrivateKey.generate()
        eph_pub_raw = eph_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        shared = eph_priv.exchange(load_x25519_public(x25519_pub_b64))
        wrap_key = _derive_wrap_key(shared, hill_id)
        wrap_nonce = secrets.token_bytes(12)
        wrapped_cek = AESGCM(wrap_key).encrypt(wrap_nonce, cek, root_key.encode("utf-8"))
        recipient_slots.append(
            {
                "root_key": root_key,
                "eph_pub": b64u_encode(eph_pub_raw),
                "nonce": b64u_encode(wrap_nonce),
                "wrapped_cek": b64u_encode(wrapped_cek),
            }
        )

    manifest: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "hill_id": hill_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sender_root_key": sender_root_key,
        "sender_device_key": sender_device_key,
        "sender_device_name": sender_device_name,
        "sender_x25519_key": sender_x25519_key,
        "sender_binding_sig": sender_binding_sig,
        "nonce": b64u_encode(payload_nonce),
        "ciphertext": b64u_encode(ciphertext),
        "recipients": recipient_slots,
    }
    sig = ed25519_sign(sender_device_priv_b64, canonical_json(manifest).encode("utf-8"))
    bundle = {**manifest, "signature": sig}
    return canonical_json(bundle).encode("utf-8")


def import_encrypted_bundle(
    bundle_bytes: bytes,
    *,
    my_root_key: str,
    my_x25519_priv_b64: str,
    allowed_sender_keys: set[str] | None = None,
    revoked_devices: set[str] | None = None,
) -> dict[str, Any]:
    """Verify Ed25519 bundle signature + membership, then decrypt with the recipient X25519 key."""
    try:
        bundle = json.loads(bundle_bytes.decode("utf-8"))
    except Exception as exc:
        raise TransportError("Bundle is not valid UTF-8 JSON.") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise TransportError("Not an .anthill-bundle file.")
    if bundle.get("version") != BUNDLE_VERSION:
        raise TransportError(f"Unsupported bundle version: {bundle.get('version')}")

    sig = str(bundle.get("signature") or "")
    manifest = {k: v for k, v in bundle.items() if k != "signature"}
    sender_root = str(manifest.get("sender_root_key") or "")
    sender_dev = str(manifest.get("sender_device_key") or "")
    sender_dev_name = str(manifest.get("sender_device_name") or "")
    sender_x25519 = str(manifest.get("sender_x25519_key") or "")
    sender_binding = str(manifest.get("sender_binding_sig") or "")
    hill_id = str(manifest.get("hill_id") or "")

    if not sig or not sender_root or not sender_dev:
        raise TransportError("Bundle is missing sender identity or signature.")
    if revoked_devices and sender_dev in revoked_devices:
        raise TransportError("Bundle sender device key is revoked.")
    if allowed_sender_keys is not None and sender_root not in allowed_sender_keys:
        raise TransportError("Bundle sender is not an active member of this Hill.")
    if not verify_device_binding(
        sender_root, sender_dev, sender_x25519, sender_dev_name, sender_binding
    ):
        raise TransportError("Bundle sender device binding signature is invalid.")
    if not ed25519_verify(sender_dev, canonical_json(manifest).encode("utf-8"), sig):
        raise TransportError("Bundle Ed25519 signature verification failed.")

    recipients = manifest.get("recipients") or []
    slot = next(
        (r for r in recipients if isinstance(r, dict) and r.get("root_key") == my_root_key),
        None,
    )
    if slot is None:
        raise TransportError("This desktop is not listed as an encrypted recipient in this bundle.")

    try:
        my_priv = load_x25519_private(my_x25519_priv_b64)
        eph_pub = load_x25519_public(str(slot["eph_pub"]))
        shared = my_priv.exchange(eph_pub)
        wrap_key = _derive_wrap_key(shared, hill_id)
        cek = AESGCM(wrap_key).decrypt(
            b64u_decode(str(slot["nonce"])),
            b64u_decode(str(slot["wrapped_cek"])),
            my_root_key.encode("utf-8"),
        )
        aad = f"{BUNDLE_FORMAT}:{BUNDLE_VERSION}:{hill_id}".encode()
        plaintext = AESGCM(cek).decrypt(
            b64u_decode(str(manifest["nonce"])),
            b64u_decode(str(manifest["ciphertext"])),
            aad,
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise TransportError("Failed to decrypt .anthill-bundle payload.") from exc

    return {
        "hill_id": hill_id,
        "sender_root_key": sender_root,
        "sender_device_key": sender_dev,
        "heads": payload.get("heads") or {},
        "records": payload.get("records") or [],
        "blobs": payload.get("blobs") or {},
        "device_bindings": payload.get("device_bindings") or {},
    }


# -- Transport 2: Direct LAN / Peer HTTP (Ed25519 signed headers) --------------


def _http_signing_bytes(
    method: str, path: str, timestamp: str, nonce: str, body_bytes: bytes
) -> bytes:
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()


def sign_http_request(
    method: str,
    path: str,
    body_bytes: bytes,
    *,
    root_key: str,
    device_key: str,
    device_priv_b64: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = timestamp or str(int(time.time()))
    nc = nonce or secrets.token_hex(12)
    sig = ed25519_sign(device_priv_b64, _http_signing_bytes(method, path, ts, nc, body_bytes))
    return {
        "X-Anthill-Root-Key": root_key,
        "X-Anthill-Node-Device": device_key,
        "X-Anthill-Timestamp": ts,
        "X-Anthill-Nonce": nc,
        "X-Anthill-Signature": sig,
    }


def verify_http_request(
    method: str,
    path: str,
    body_bytes: bytes,
    headers: Mapping[str, str],
    *,
    pinned_device_keys_by_root: dict[str, set[str]] | None = None,
    revoked_devices: set[str] | None = None,
    max_skew_seconds: int = 120,
    seen_nonces: set[str] | None = None,
) -> tuple[bool, str, str, str]:
    """Verify signed peer HTTP request headers. Returns ``(ok, reason, root_key, device_key)``."""
    lower = {k.lower(): v for k, v in headers.items()}
    root_key = lower.get("x-anthill-root-key", "").strip()
    device_key = lower.get("x-anthill-node-device", "").strip()
    ts = lower.get("x-anthill-timestamp", "").strip()
    nonce = lower.get("x-anthill-nonce", "").strip()
    sig = lower.get("x-anthill-signature", "").strip()

    if not all((root_key, device_key, ts, nonce, sig)):
        return False, "missing required peer authentication headers", "", ""
    try:
        ts_int = int(ts)
    except ValueError:
        return False, "invalid timestamp header", root_key, device_key
    if abs(int(time.time()) - ts_int) > max_skew_seconds:
        return False, "request timestamp outside replay window", root_key, device_key
    if seen_nonces is not None:
        if nonce in seen_nonces:
            return False, "replayed request nonce", root_key, device_key
        if len(seen_nonces) > 4096:
            seen_nonces.clear()
        seen_nonces.add(nonce)
    if revoked_devices and device_key in revoked_devices:
        return False, "peer device key is revoked", root_key, device_key
    if pinned_device_keys_by_root is not None:
        allowed_devs = pinned_device_keys_by_root.get(root_key)
        if not allowed_devs or device_key not in allowed_devs:
            return False, "peer key is not pinned for this node", root_key, device_key
    if not ed25519_verify(
        device_key, _http_signing_bytes(method, path, ts, nonce, body_bytes), sig
    ):
        return False, "invalid Ed25519 HTTP request signature", root_key, device_key
    return True, "ok", root_key, device_key


class HttpPeerTransport:
    """Direct peer-to-peer HTTP client using signed Ed25519 headers."""

    name = "lan"

    def __init__(
        self,
        base_url: str,
        *,
        root_key: str,
        device_key: str,
        device_priv_b64: str,
        timeout: float = 15.0,
        client_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.root_key = root_key
        self.device_key = device_key
        self.device_priv_b64 = device_priv_b64
        self.timeout = timeout
        self._client_transport = client_transport

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = canonical_json(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **sign_http_request(
                "POST",
                path,
                body,
                root_key=self.root_key,
                device_key=self.device_key,
                device_priv_b64=self.device_priv_b64,
            ),
        }
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                trust_env=False,
                transport=self._client_transport,
            ) as client:
                resp = client.post(path, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportError(f"Could not reach peer at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("detail") or "")
            except Exception:
                detail = resp.text[:200]
            raise TransportError(f"Peer rejected {path} ({resp.status_code}): {detail}")
        return dict(resp.json())

    def ping(self, hill_id: str) -> dict[str, Any]:
        return self._post("/api/node/p2p/ping", {"hill_id": hill_id})

    def fetch_heads(self, hill_id: str) -> dict[str, int]:
        data = self._post("/api/node/p2p/heads", {"hill_id": hill_id})
        return {str(k): int(v) for k, v in (data.get("heads") or {}).items()}

    def exchange_records(
        self,
        hill_id: str,
        local_heads: dict[str, int],
        outgoing_records: list[dict[str, Any]],
        outgoing_blobs: dict[str, str],
        device_bindings: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        return self._post(
            "/api/node/p2p/sync",
            {
                "hill_id": hill_id,
                "heads": local_heads,
                "records": outgoing_records,
                "blobs": outgoing_blobs,
                "device_bindings": device_bindings,
            },
        )

    def consult(self, hill_id: str, question: str, draft: str) -> dict[str, Any]:
        return self._post(
            "/api/node/p2p/consult",
            {"hill_id": hill_id, "question": question, "draft": draft},
        )

    def send_pair_request(self, join_response: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/node/p2p/pair/request", join_response)

    def send_pair_finalize(self, nonce: str, hill_id: str) -> dict[str, Any]:
        return self._post("/api/node/p2p/pair/finalize", {"nonce": nonce, "hill_id": hill_id})
