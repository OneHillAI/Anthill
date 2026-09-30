"""Model-free tests for Phase 3 components."""

import time

import numpy as np
import pytest

from anthill.orchestrator.registry import NodeRegistry
from anthill.wiki.ask import _answer_covers_question

# ── inference router ──────────────────────────────────────────────────────────


def test_route_returns_least_loaded():
    reg = NodeRegistry()
    reg.register("n1", "http://n1:9001", "qwen2.5:3b")
    reg.register("n2", "http://n2:9001", "qwen2.5:3b")
    reg.heartbeat("n1", load=3)
    reg.heartbeat("n2", load=1)
    node = reg.route("qwen2.5:3b")
    assert node is not None
    assert node.node_id == "n2"


def test_route_filters_by_model():
    reg = NodeRegistry()
    reg.register("a", "http://a:9001", "qwen2.5:3b")
    reg.register("b", "http://b:9001", "llama3:8b")
    node = reg.route("llama3:8b")
    assert node is not None and node.node_id == "b"


def test_route_returns_none_when_no_match():
    reg = NodeRegistry()
    reg.register("a", "http://a:9001", "qwen2.5:3b")
    assert reg.route("nonexistent-model") is None


def test_route_ignores_dead_nodes():
    reg = NodeRegistry()
    reg.register("dead", "http://dead:9001", "qwen2.5:3b")
    # Force the node to appear dead by backdating its last_seen
    reg._nodes["dead"].last_seen = time.monotonic() - 9999
    assert reg.route("qwen2.5:3b") is None


def test_heartbeat_updates_load():
    reg = NodeRegistry()
    reg.register("n1", "http://n1:9001", "qwen2.5:3b")
    assert reg._nodes["n1"].load == 0
    reg.heartbeat("n1", load=5)
    assert reg._nodes["n1"].load == 5


# ── central index ─────────────────────────────────────────────────────────────


def test_central_index_publish_and_search(tmp_path):
    from anthill.orchestrator.central_index import CentralIndex

    idx = CentralIndex(tmp_path / "db")
    vec = np.random.randn(1024).astype(np.float32)
    vec /= np.linalg.norm(vec)
    idx.publish("node-1", vec.tolist(), "PostgreSQL was chosen.")
    hits = idx.search(vec.tolist(), threshold=0.90)
    assert len(hits) == 1
    assert hits[0]["answer"] == "PostgreSQL was chosen."
    assert hits[0]["node_id"] == "node-1"
    assert hits[0]["similarity"] >= 0.90


def test_central_index_threshold_filters():
    """A very different vector should not exceed the threshold."""
    pytest.importorskip("lancedb")
    import tempfile

    from anthill.orchestrator.central_index import CentralIndex

    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path

        idx = CentralIndex(Path(d))
        stored = np.ones(1024, dtype=np.float32)
        stored /= np.linalg.norm(stored)
        idx.publish("node-1", stored.tolist(), "answer A")
        # Orthogonal vector → cosine ~0
        query = np.zeros(1024, dtype=np.float32)
        query[0] = 1.0
        hits = idx.search(query.tolist(), threshold=0.93)
        assert hits == []


# ── answer-covers-question guard ──────────────────────────────────────────────


def test_guard_passes_when_answer_covers():
    assert _answer_covers_question(
        "which database for billing",
        "We chose PostgreSQL for the billing database due to ACID guarantees.",
    )


def test_guard_fails_when_answer_misses_topic():
    # Answer about auth, question about database
    assert not _answer_covers_question(
        "which database for billing",
        "We use JWT tokens with bcrypt hashing for authentication.",
    )
