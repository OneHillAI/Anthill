"""Reachability checks for Tier 5 distributed local pooling (#661).

llama.cpp's ``rpc-server`` speaks a raw, non-HTTP wire protocol - there is nothing here Anthill can
send a real request to and parse a meaningful reply from. All this module can honestly say is whether
*something* is listening on a worker's port, never that the rpc-server itself is healthy. Anthill also
never launches, manages, or connects to these processes for inference - the pool's main node's actual
chat traffic goes through the existing OpenAI-compatible ``org_model_endpoint`` path unchanged; this
module is purely for the settings page's "is this worker up" display.
"""

from __future__ import annotations

import socket
from collections.abc import Callable


def tcp_reachable(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """True if a TCP connection to ``host:port`` opens within ``timeout`` seconds.

    Never raises - a bad host, an unreachable network, or a closed port all just return False. A True
    result means "a port is open," not "the rpc-server on it is healthy" - rpc-server's protocol isn't
    HTTP, so nothing more specific can be checked from here.
    """
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def worker_reachability(
    workers: list[dict], *, check: Callable[[str, int], bool] | None = None
) -> list[dict]:
    """Each worker dict plus a ``reachable: bool``, via ``check`` (injectable for tests - defaults to
    ``tcp_reachable``, resolved at call time via the module global rather than as a bound default
    argument, so monkeypatching ``anthill.hosting.cluster.tcp_reachable`` in a test actually takes
    effect for callers that never pass ``check`` explicitly). Original fields are preserved; a
    missing/non-numeric port is treated as unreachable rather than raising."""
    check = check or tcp_reachable
    out: list[dict] = []
    for w in workers:
        host = str(w.get("host", ""))
        try:
            port = int(w.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        reachable = check(host, port) if host and port else False
        out.append({**w, "reachable": reachable})
    return out
