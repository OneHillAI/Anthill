"""The promotion gate's scoring subset must be bounded, deterministic, and order-independent.

Issue #365: `evaluate_models` used to score `examples[:sample]` - the first N in whatever order the
train/eval split produced. When more held-out examples are available than `sample`, it now draws a
reproducible pseudo-random subset instead, so the gate isn't biased by ordering.
"""

import anthill.cache.embedder as emb
from anthill.lifecycle.evaluate import evaluate_models


class _RecordingBackend:
    """Stands in for OllamaBackend: records the instruction of every scored example."""

    def __init__(self):
        self.seen = []

    def chat(self, msgs, *, model):
        self.seen.append(msgs[0].content)
        return f"ans::{model}::{msgs[0].content}"


def _mock_embed(monkeypatch):
    # Deterministic, dependency-free stand-ins for the sentence-transformer embedder.
    monkeypatch.setattr(
        emb, "embed", lambda text: [float(len(text)), float(sum(map(ord, text[:8])))]
    )
    monkeypatch.setattr(emb, "cosine", lambda a, b: 0.5)


def test_scores_at_most_sample_examples(monkeypatch):
    _mock_embed(monkeypatch)
    ex = [(f"q{n}", f"a{n}") for n in range(25)]
    be = _RecordingBackend()
    res = evaluate_models(be, "A", "B", ex, sample=20)
    assert res.n == 20
    assert len(set(be.seen)) == 20  # exactly `sample` distinct instructions scored


def test_subset_is_deterministic_and_not_first_n(monkeypatch):
    _mock_embed(monkeypatch)
    ex = [(f"q{n}", f"a{n}") for n in range(25)]
    b1, b2 = _RecordingBackend(), _RecordingBackend()
    evaluate_models(b1, "A", "B", ex, sample=20)
    evaluate_models(b2, "A", "B", ex, sample=20)
    s1, s2 = set(b1.seen), set(b2.seen)
    assert s1 == s2  # reproducible across runs
    assert s1 != {f"q{n}" for n in range(20)}  # an order-independent draw, not just the first N


def test_small_set_scores_all(monkeypatch):
    _mock_embed(monkeypatch)
    ex = [(f"q{n}", f"a{n}") for n in range(4)]
    be = _RecordingBackend()
    res = evaluate_models(be, "A", "B", ex, sample=20)
    assert res.n == 4
    assert set(be.seen) == {f"q{n}" for n in range(4)}  # fewer than `sample`: all are scored
