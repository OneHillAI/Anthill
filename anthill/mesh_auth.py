"""Shared-secret auth for the distributed mesh (orchestrator hub + node agents).

Security review: the mesh endpoints that seed org-trusted state - registering a node, promoting a page
into the org wiki, publishing into the central semantic index - had no auth at all. Any client that could
reach the orchestrator could register a node and then promote arbitrary content that every agent later
grounds on (a supply-chain hole), or read peers' cached answers. The mesh is Phase 3 - Ed25519-signed
manifests are the eventual design; this is the interim shared-secret gate.

Set ``ANTHILL_MESH_TOKEN`` to the SAME value on the orchestrator and every node. When it is set, the
gated endpoints require ``Authorization: Bearer <token>`` (constant-time compare) and the clients present
it automatically.

**Secure by default:** when no token is set the gate is now CLOSED (401), not open - so an orchestrator
that is network-exposed without a token cannot silently accept node registrations, wiki promotions, or
central-cache reads/writes. A standard single-node deployment never calls these endpoints, so this
changes nothing for it. A deployment that genuinely wants an unauthenticated mesh (a trusted single-node
or dev run) must opt in explicitly with ``ANTHILL_MESH_ALLOW_INSECURE=1`` - a conscious choice rather
than a silent open boundary. (Phase 3 replaces the shared secret with Ed25519-signed manifests.)
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Header, HTTPException

log = logging.getLogger("anthill.mesh")
_warned = False


def _token() -> str:
    return os.environ.get("ANTHILL_MESH_TOKEN", "").strip()


def _allow_insecure() -> bool:
    return os.environ.get("ANTHILL_MESH_ALLOW_INSECURE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def require_mesh(authorization: str = Header(default="")) -> None:
    """FastAPI dependency guarding a mesh endpoint. Enforced when ``ANTHILL_MESH_TOKEN`` is set. When
    it is unset the gate is CLOSED by default (401) unless ``ANTHILL_MESH_ALLOW_INSECURE`` is set, in
    which case it is a no-op that warns once. Raises 401 on a missing/incorrect token."""
    global _warned
    token = _token()
    if not token:
        if _allow_insecure():
            if not _warned:
                log.warning(
                    "ANTHILL_MESH_TOKEN is unset and ANTHILL_MESH_ALLOW_INSECURE is set - mesh "
                    "endpoints (node registration, wiki promotion, central cache) are UNAUTHENTICATED. "
                    "Use this only on a trusted single-node or dev run."
                )
                _warned = True
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "mesh endpoint locked: set ANTHILL_MESH_TOKEN (same value on the orchestrator and every "
                "node), or ANTHILL_MESH_ALLOW_INSECURE=1 for a trusted single-node/dev run"
            ),
        )
    presented = authorization
    if presented.lower().startswith("bearer "):
        presented = presented[7:]
    if not hmac.compare_digest(presented.strip(), token):
        raise HTTPException(status_code=401, detail="invalid or missing mesh token")


def mesh_headers() -> dict[str, str]:
    """Auth header for a client call to a mesh peer. Empty when no token is configured (so an
    unauthenticated single-node run keeps working unchanged)."""
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}
