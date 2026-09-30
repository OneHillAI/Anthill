"""Memory layer: extraction parsing, dedup, encode/decode, recall ranking (no network)."""

import numpy as np

from anthill import memory as mem


class _Backend:
    """Fake inference backend returning a canned chat response."""

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply


def test_extract_parses_json_array():
    b = _Backend('["X uses Postgres for billing.", "X prefers metric units."]')
    items = mem.extract("…transcript…", b)
    assert items == ["X uses Postgres for billing.", "X prefers metric units."]


def test_extract_parses_json_embedded_in_prose():
    b = _Backend('Sure! Here is what I found:\n["We ship on Fridays."]\nHope that helps.')
    assert mem.extract("t", b) == ["We ship on Fridays."]


def test_extract_falls_back_to_bullets():
    b = _Backend("- We use Stripe for payments\n- The CFO is Dana")
    items = mem.extract("t", b)
    assert "We use Stripe for payments" in items and "The CFO is Dana" in items


def test_extract_caps_and_dedupes():
    b = _Backend('["a fact here", "a fact here", "another distinct fact"]')
    items = mem.extract("t", b, max_items=5)
    assert items == ["a fact here", "another distinct fact"]


def test_extract_empty_on_backend_error():
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("model down")

    assert mem.extract("t", _Boom()) == []


def test_is_new_rejects_duplicates_and_substrings():
    existing = ["We use Postgres for billing."]
    assert mem.is_new("Totally different fact about hiring.", existing) is True
    assert mem.is_new("we use postgres for billing", existing) is False  # normalised dup
    assert (
        mem.is_new("We use Postgres for billing in the EU region.", existing) is False
    )  # superstring


def test_encode_decode_roundtrip():
    v = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = mem.decode_vec(mem.encode_vec(v))
    assert np.allclose(out, v)
    assert mem.encode_vec(None) == "" and mem.decode_vec("") is None


def test_recall_ranks_by_cosine():
    # three unit-ish vectors; query closest to id 2
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    c = np.array([0.9, 0.1, 0.0], dtype=np.float32)
    items = [
        (1, "about A", mem.encode_vec(a)),
        (2, "about C", mem.encode_vec(c)),
        (3, "about B", mem.encode_vec(b)),
    ]
    hits = mem.recall(items, "q", a, k=2, min_score=0.0)
    assert [h[0] for h in hits] == [1, 2]  # A (1.0) then C (0.9) beat B (0.0)


def test_recall_keyword_fallback_without_vectors():
    items = [
        (1, "billing runs on postgres", ""),
        (2, "the office is in berlin", ""),
    ]
    hits = mem.recall(items, "which database for billing", None, k=1, min_score=0.1)
    assert hits and hits[0][0] == 1


def test_recall_empty_when_nothing_passes_threshold():
    items = [(1, "unrelated text", "")]
    assert mem.recall(items, "completely different query terms", None, min_score=0.9) == []
