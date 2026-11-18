"""The gate that decides whether a passage of a document is relevant enough to go in an answer's context
(`doc_index.search(..., min_rare=2)`), pinned on the set it was measured on: every question a document answers
finds its passage among the best two, and no question the documents do not answer finds any. The gate counts rare
words, not a BM25 score, so the same set must hold for a wiki of one document and a wiki of four."""

import random

import pytest

from anthill.wiki.doc_index import DocIndex
from tests.doc_gate_corpus import DOCS, QA, UNRELATED


def _index(tmp_path, names) -> DocIndex:
    idx = DocIndex(tmp_path / "cache", tmp_path / "text")
    idx.text_dir.mkdir()
    for name in names:
        (idx.text_dir / f"{name}.txt").write_text(DOCS[name], encoding="utf-8")
        idx.index(name, DOCS[name], pdf=name.endswith(".pdf"))
    return idx


@pytest.mark.parametrize(
    "names", [list(DOCS), ["hollis-bakery-handbook.pdf"]], ids=["four documents", "one document"]
)
def test_the_questions_a_document_answers_find_their_passage_and_the_others_find_nothing(
    tmp_path, names
):
    idx = _index(tmp_path, names)
    for question, answer in QA:
        if not any(answer.lower() in DOCS[n].lower() for n in names):
            continue  # its document is not in this wiki
        hits = idx.search(question, 2, min_rare=2)
        assert any(answer.lower() in h.text.lower() for h in hits), question
    for question in UNRELATED:
        assert idx.search(question, 2, min_rare=2) == [], question


def test_without_the_gate_the_same_unrelated_questions_do_find_passages(tmp_path):
    """The reason for the gate: plain keyword ranking returns something for questions the documents do not
    answer, because a passage shares a word such as "short" or "morning" with them."""
    idx = _index(tmp_path, list(DOCS))
    assert any(idx.search(q, 2) for q in UNRELATED)


def test_a_passage_that_only_shares_common_words_is_not_a_hit(tmp_path):
    idx = _index(tmp_path, list(DOCS))
    assert (
        idx.search("what is the way to the club", 2, min_rare=2) == []
    )  # "what", "is", "the", "to", "way"...


def test_accents_do_not_hide_the_rare_words(tmp_path):
    idx = DocIndex(tmp_path / "cache", tmp_path / "text")
    idx.text_dir.mkdir()
    text = "Le café crème est servi à la terrasse du boulodrome.\n\n" + "Autre chose. " * 120
    (idx.text_dir / "a.html.txt").write_text(text, encoding="utf-8")
    idx.index("a.html", text)
    assert idx.search("cafe creme terrasse", 2, min_rare=2)


def _other_documents(n: int) -> list[str]:
    """Documents about other things: random tokens, English function words, and words the questions also use."""
    rng = random.Random(0)
    function = [
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "at",
        "for",
        "with",
        "on",
        "as",
        "by",
        "it",
        "this",
        "that",
        "from",
        "or",
        "be",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "do",
        "does",
        "what",
        "how",
        "which",
        "who",
        "when",
        "where",
        "why",
        "not",
        "can",
        "will",
        "would",
        "should",
    ]
    content = [
        "price",
        "club",
        "bakery",
        "notice",
        "deposit",
        "order",
        "member",
        "policy",
        "rent",
        "delivery",
        "report",
        "team",
        "year",
        "day",
        "time",
        "water",
        "point",
        "level",
        "job",
        "three",
        "give",
        "tips",
        "interview",
        "boiling",
        "sea",
    ]
    vocab = [f"w{i}" for i in range(4000)] + function * 60 + content * 8
    return [
        "\n\n".join(
            " ".join(rng.choice(vocab) for _ in range(rng.randint(30, 120))) + "." for _ in range(8)
        )
        for _ in range(n)
    ]


def test_the_gate_in_a_wiki_of_fifty_documents_about_other_things(tmp_path):
    """Measured on generated wikis of 50, 200 and 500 documents: the answers still found (16 of 16 at 50, 15 of 16
    at 200 and 500) and at most 2 of the 10 unrelated questions getting a passage by coincidence. A BM25 score floor
    did not survive the change of size. Generated, not real, documents: the bounds are what was measured."""
    idx = _index(tmp_path, list(DOCS))
    for i, text in enumerate(_other_documents(46)):
        (idx.text_dir / f"other-{i}.pdf.txt").write_text(text, encoding="utf-8")
        idx.index(f"other-{i}.pdf", text, pdf=True)
    found = sum(
        any(a.lower() in h.text.lower() for h in idx.search(q, 2, min_rare=2)) for q, a in QA
    )
    assert found >= 15
    assert sum(bool(idx.search(q, 2, min_rare=2)) for q in UNRELATED) <= 2
