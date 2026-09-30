"""MMR reranking of retrieved grounding (inference-opt P1): when several relevant pages are found,
pick ones that are relevant AND add something new, so the (cache-stable) grounding block covers more
ground instead of k near-duplicates. Model-free - operates on the page vectors already computed for
ranking. See engineering-plans/INFERENCE_OPTIMIZATION.md."""

from pathlib import Path

import numpy as np

from anthill.wiki import ask


def _v(*xs):
    a = np.array(xs, dtype=float)
    n = np.linalg.norm(a)
    return a / (n or 1.0)  # L2-normalise so emb.cosine (a dot product) is a real cosine


def test_mmr_prefers_diversity_over_a_near_duplicate():
    v1 = _v(1, 0, 0)
    v2 = _v(0.98, 0.2, 0)  # ~ a near-duplicate of p1
    v3 = _v(0.3, 0.95, 0)  # relevant but a different direction (adds new ground)
    scored = [(0.90, Path("p1"), v1), (0.88, Path("p2"), v2), (0.80, Path("p3"), v3)]
    picked = [p.name for p in ask._mmr_select(scored, k=2, lam=0.5)]
    assert picked[0] == "p1"  # the most relevant page still seeds the set
    assert picked[1] == "p3"  # ... then diversity beats the near-duplicate p2


def test_mmr_lambda_one_is_pure_relevance_order():
    v = _v(1, 0, 0)
    scored = [(0.9, Path("a"), v), (0.8, Path("b"), v), (0.7, Path("c"), v)]
    assert [p.name for p in ask._mmr_select(scored, k=2, lam=1.0)] == ["a", "b"]


def test_mmr_returns_all_when_k_exceeds_candidates():
    scored = [(0.9, Path("a"), _v(1, 0, 0))]
    assert [p.name for p in ask._mmr_select(scored, k=5)] == ["a"]
    assert ask._mmr_select(scored, k=0) == []


def test_mmr_is_deterministic():
    scored = [
        (0.9, Path("a"), _v(1, 0, 0)),
        (0.85, Path("b"), _v(0, 1, 0)),
        (0.8, Path("c"), _v(0, 0, 1)),
    ]
    r1 = [p.name for p in ask._mmr_select(scored, k=3, lam=0.6)]
    r2 = [p.name for p in ask._mmr_select(scored, k=3, lam=0.6)]
    assert r1 == r2 and set(r1) == {"a", "b", "c"}


def test_mmr_lambda_env_default_is_sane():
    assert 0.0 <= ask._MMR_LAMBDA <= 1.0
