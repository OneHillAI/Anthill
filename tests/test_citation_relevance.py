"""Citations under a chat answer are the retrieved pages that are clearly about the question, not every
page that cleared the grounding floor (#152). The model's grounding is unchanged. Model-free: embeddings
are faked with a keyword vector."""

import numpy as np
import pytest

from anthill.cache import embedder as emb
from anthill.lifecycle.version import tag_page
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace

COURIER = "# Interview: courier (P11), 19 Sep 2026\n\nThe courier said map pins are wrong in the old town.\n"
BAKERY = (
    "# Interview: independent bakery owner (P07)\n\nThe bakery owner wants fewer order emails.\n"
)
# One off-topic word makes the retitled copy score a little below the original, so the original wins.
REDESIGN = (
    "# Orders App Redesign\n\nThe courier said map pins are wrong in the old town. A bakery too.\n"
)
QUESTION = "What did the courier say about maps?"


def _fake_embed(text: str) -> np.ndarray:
    t = text.lower()
    v = np.array([t.count("courier") + t.count("map"), t.count("bakery"), 1.5], dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    monkeypatch.setattr(emb, "embed", _fake_embed)
    monkeypatch.setattr(emb, "safe_embed", _fake_embed)
    monkeypatch.setattr(emb, "available", lambda: True)

    class _NoCache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            self.stored = slugs

    monkeypatch.setattr(ask_mod, "SemanticCache", _NoCache)


def _page(root, name, body, source=None):
    path = root / f"{name}.md"
    path.write_text(tag_page(body, "m", on="2026-09-19", source=source) if source else body)
    return path


def _scored(root, specs):
    return [(s, _page(root, n, b, src)) for s, n, b, src in specs]


def _stems(paths):
    return [p.stem for p in paths]


def test_unrelated_pages_are_dropped(tmp_path):
    scored = _scored(
        tmp_path,
        [
            (0.62, "courier", COURIER, None),
            (0.40, "bakery", BAKERY, None),
            (0.38, "other", "# x\n", None),
        ],
    )
    assert _stems(ask_mod._filter_citations(scored)) == ["courier"]


def test_the_top_page_is_always_kept(tmp_path):
    scored = _scored(tmp_path, [(0.36, "only", COURIER, None)])
    assert _stems(ask_mod._filter_citations(scored)) == ["only"]
    assert ask_mod._filter_citations([]) == []


def test_the_top_page_is_kept_even_when_it_is_not_first(tmp_path):
    scored = _scored(tmp_path, [(0.40, "bakery", BAKERY, None), (0.70, "courier", COURIER, None)])
    assert _stems(ask_mod._filter_citations(scored)) == ["courier"]


def test_near_ties_are_all_kept_in_retrieval_order(tmp_path):
    margin = ask_mod.CITATION_SIM_MARGIN
    scored = _scored(
        tmp_path,
        [
            (0.60, "a", "# a\n", None),
            (0.60 - margin / 2, "b", "# b\n", None),
            (0.60 - margin, "c", "# c\n", None),
            (0.60 - margin - 0.01, "d", "# d\n", None),
        ],
    )
    assert _stems(ask_mod._filter_citations(scored)) == ["a", "b", "c"]


def test_pages_from_one_source_are_cited_once_by_the_best_scoring(tmp_path):
    scored = _scored(
        tmp_path,
        [
            (0.60, "orders-app-redesign", REDESIGN, "note-abc123.md"),
            (0.62, "interview-courier", COURIER, "note-abc123.md"),
            (0.61, "other-note", BAKERY, "other-def456.md"),
        ],
    )
    assert _stems(ask_mod._filter_citations(scored)) == ["interview-courier", "other-note"]


def test_pages_without_a_source_are_never_grouped(tmp_path):
    scored = _scored(tmp_path, [(0.6, "a", "# a\n", None), (0.6, "b", "# a\n", None)])
    assert _stems(ask_mod._filter_citations(scored)) == ["a", "b"]


def test_a_dropped_duplicate_does_not_hide_a_kept_page_of_the_same_source(tmp_path):
    scored = _scored(
        tmp_path,
        [
            (0.70, "top", COURIER, "x-1.md"),
            (0.30, "dup", REDESIGN, "x-1.md"),
        ],
    )
    assert _stems(ask_mod._filter_citations(scored)) == ["top"]


class _Backend:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **k):
        self.prompts.append(" ".join(m.content for m in messages))
        return "The courier said map pins are wrong in the old town."

    def chat_stream(self, messages, **k):
        self.prompts.append(" ".join(m.content for m in messages))
        yield "The courier said map pins are wrong in the old town."


@pytest.fixture
def interview_wiki(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("interview-courier-p11", tag_page(COURIER, "m", on="2026-09-19", source="c-1.md"))
    ws.write_page("interview-bakery-p07", tag_page(BAKERY, "m", on="2026-09-12", source="b-2.md"))
    ws.write_page("orders-app-redesign", tag_page(REDESIGN, "m", on="2026-09-19", source="c-1.md"))
    return ws


def _assert_model_saw_every_page(prompt):
    assert "map pins are wrong" in prompt
    assert "fewer order emails" in prompt


def test_ask_cites_only_the_interview_asked_about_but_grounds_on_every_page(interview_wiki):
    backend = _Backend()
    _answer, slugs, _hit = ask_mod.ask(interview_wiki, QUESTION, backend)
    assert slugs == ["interview-courier-p11"]
    _assert_model_saw_every_page(backend.prompts[0])


def test_ask_stream_cites_only_the_interview_asked_about_but_grounds_on_every_page(interview_wiki):
    backend, seen = _Backend(), []
    list(ask_mod.ask_stream(interview_wiki, QUESTION, backend, on_context=seen.extend))
    assert seen == ["interview-courier-p11"]
    _assert_model_saw_every_page(backend.prompts[0])


def test_the_cited_pages_are_a_subset_of_the_retrieved_pages(interview_wiki):
    retrieved = ask_mod._merge_relevant([interview_wiki], QUESTION, 3, _fake_embed(QUESTION))
    assert sorted(_stems(retrieved)) == [
        "interview-bakery-p07",
        "interview-courier-p11",
        "orders-app-redesign",
    ]
    cited = ask_mod._cited_slugs(retrieved, [interview_wiki], _fake_embed(QUESTION))
    assert set(cited) < set(_stems(retrieved))


def test_the_citations_are_also_what_a_later_cache_hit_reports(interview_wiki, monkeypatch):
    stored = {}

    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            stored["slugs"] = slugs

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    _answer, slugs, _hit = ask_mod.ask(interview_wiki, QUESTION, _Backend())
    assert stored["slugs"] == slugs


def test_without_a_question_vector_every_retrieved_page_is_cited(tmp_path):
    pages = [_page(tmp_path, "a", "# a\n"), _page(tmp_path, "b", "# b\n")]
    assert ask_mod._cited_slugs(pages, [], None) == ["a", "b"]


def test_a_scoring_failure_falls_back_to_citing_every_retrieved_page(tmp_path, monkeypatch):
    pages = [_page(tmp_path, "a", "# a\n"), _page(tmp_path, "b", "# b\n")]

    def _boom(*a, **k):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(ask_mod, "_page_vectors", _boom)
    assert ask_mod._cited_slugs(pages, [], _fake_embed("x")) == ["a", "b"]
