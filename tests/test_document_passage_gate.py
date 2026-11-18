"""The gate that decides whether a passage of a document is relevant enough to go in an answer's context
(`doc_index.search(..., min_rare=2)`), pinned on the set it was measured on: every question a document answers
finds its passage among the best two, and no question the documents do not answer finds any. The gate counts rare
words, not a BM25 score, so the same set must hold for a wiki of one document and a wiki of four."""

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
