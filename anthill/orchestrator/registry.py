from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

HEARTBEAT_TIMEOUT = 60.0  # seconds; 12× the 5s heartbeat interval


@dataclass
class NodeInfo:
    node_id: str
    base_url: str  # how other nodes reach this node's agent
    model: str
    load: int = 0  # inflight request count, updated on each heartbeat
    last_seen: float = field(default_factory=time.monotonic)

    def is_alive(self) -> bool:
        return (time.monotonic() - self.last_seen) < HEARTBEAT_TIMEOUT


class NodeRegistry:
    """In-memory registry of live node agents.

    Thread-safe; state resets on restart.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeInfo] = {}
        self._lock = threading.Lock()

    def register(self, node_id: str, base_url: str, model: str) -> None:
        with self._lock:
            if node_id in self._nodes:
                self._nodes[node_id].last_seen = time.monotonic()
                self._nodes[node_id].base_url = base_url
                self._nodes[node_id].model = model
            else:
                self._nodes[node_id] = NodeInfo(node_id, base_url, model)

    def heartbeat(self, node_id: str, load: int = 0) -> bool:
        with self._lock:
            if node_id not in self._nodes:
                return False
            self._nodes[node_id].last_seen = time.monotonic()
            self._nodes[node_id].load = load
            return True

    def route(self, model: str) -> NodeInfo | None:
        """Return the least-loaded live node that serves `model`, or None."""
        with self._lock:
            candidates = [n for n in self._nodes.values() if n.is_alive() and n.model == model]
        if not candidates:
            return None
        return min(candidates, key=lambda n: n.load)

    def live_nodes(self) -> list[NodeInfo]:
        with self._lock:
            return [n for n in self._nodes.values() if n.is_alive()]

    def all_nodes(self) -> list[NodeInfo]:
        with self._lock:
            return list(self._nodes.values())
