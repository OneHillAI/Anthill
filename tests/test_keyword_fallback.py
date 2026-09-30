"""wiki/ask.py's _keyword_fallback(): the last-resort retrieval path when both Meilisearch and real
embeddings are unavailable (anthill/cache/embedder.py's Ollama-backed available()/embed() genuinely
raising is what makes this path reliably reachable in tests now - see test_ask_stream.py's grounding
test for how this used to silently depend on whatever embedding backend happened to be installed
locally). Regression: a 2-letter query term used to be dropped outright (`len(t) > 2`), so a real
technical abbreviation like "db" never matched anything, even a page titled exactly that.
"""

from anthill.wiki.ask import _keyword_fallback
from anthill.wiki.workspace import Workspace


def _ws(tmp_path, pages: dict[str, str]) -> Workspace:
    ws = Workspace(tmp_path / "w")
    ws.init()
    for slug, body in pages.items():
        ws.write_page(slug, body)
    return ws


def test_two_letter_technical_term_matches(tmp_path):
    ws = _ws(
        tmp_path,
        {
            "DB": "# DB\n\nWe use Postgres.\n",
            "Office": "# Office\n\nWe are based in Berlin.\n",
        },
    )
    hits = _keyword_fallback(ws, "what db?", k=3)
    assert [p.stem for p in hits] == ["db"]


def test_single_letter_terms_are_still_dropped(tmp_path):
    # Not every short token is meaningful - a single letter carries essentially no signal.
    ws = _ws(tmp_path, {"A": "# A\n\na a a\n", "B": "# B\n\nb b b\n"})
    hits = _keyword_fallback(ws, "a b", k=3)
    assert hits == []


def test_normal_length_terms_still_match_as_before(tmp_path):
    ws = _ws(
        tmp_path,
        {"Billing": "# Billing\n\nRuns on Postgres.\n", "Office": "# Office\n\nIn Berlin.\n"},
    )
    hits = _keyword_fallback(ws, "which database for billing", k=3)
    assert [p.stem for p in hits] == ["billing"]


def test_ranks_by_total_term_count(tmp_path):
    ws = _ws(
        tmp_path,
        {
            "Strong": "# Strong\n\ndb db db - all about the db.\n",
            "Weak": "# Weak\n\nmentions db once.\n",
        },
    )
    hits = _keyword_fallback(ws, "db", k=3)
    assert [p.stem for p in hits] == ["strong", "weak"]


def test_no_match_returns_empty(tmp_path):
    ws = _ws(tmp_path, {"Office": "# Office\n\nWe are based in Berlin.\n"})
    assert _keyword_fallback(ws, "unrelated query terms here", k=3) == []
