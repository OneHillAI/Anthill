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


# Where the node agent listens decides who may call it without a token. ``anthill serve`` reads the address from
# ``ANTHILL_NODE_HOST`` (default 127.0.0.1). A node that listens on loopback only is reachable from this machine
# alone (a local tool such as Cursor or Continue pointed at it), so it needs no token. A node that listens
# anywhere else needs the mesh token from every caller. This is a rule about the bind address, not a guess about
# each request, so it does not depend on which headers a proxy adds.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def node_bind_is_loopback() -> bool:
    host = os.environ.get("ANTHILL_NODE_HOST", "127.0.0.1").strip().lower().strip("[]")
    return host in _LOOPBACK


def require_mesh_unless_local(authorization: str = Header(default="")) -> None:
    """Guard for a node-agent route that a local tool uses (the OpenAI-compatible chat API). A node that
    listens on loopback only answers with no token; a node that listens beyond it requires the mesh token
    from every caller. With no token set and no insecure opt-in, an exposed node refuses everyone."""
    if node_bind_is_loopback():
        return
    require_mesh(authorization)


def exposure_refusal(host: str) -> str:
    """Why a node agent must not listen on ``host``, or "" when it may. A non-loopback address with no mesh
    token (and no explicit insecure opt-in) would answer anyone who can reach it."""
    h = (host or "").strip().lower().strip("[]")
    if h in _LOOPBACK or _token() or _allow_insecure():
        return ""
    return (
        f"refusing to listen on {host}: set ANTHILL_MESH_TOKEN (the same value on every node), "
        "or keep the node agent on 127.0.0.1"
    )


def mesh_headers() -> dict[str, str]:
    """Auth header for a client call to a mesh peer. Empty when no token is configured (so an
    unauthenticated single-node run keeps working unchanged)."""
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}
