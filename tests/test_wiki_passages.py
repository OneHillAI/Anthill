"""Part 2 of #109: a long wiki page is cut to its best passages before it reaches the prompt. Model-free."""

import pytest

from anthill.common.text import strip_frontmatter
from anthill.wiki import ask as ask_mod
from anthill.wiki import passages
from anthill.wiki.workspace import Workspace

DEEP = "The rollback token is MAPLE-9021 and it expires after nine days."


def _long_page(deep_at=20, sections=40, title="# Operations Manual"):
    """A long page of numbered sections of unrelated text, with one distinctive fact placed deep inside."""
    parts = [title, "", "Opening summary of the manual."]
    for i in range(sections):
        parts += [
            "",
            f"## Section {i}",
            "",
            f"Routine notes for area {i}. " + "Filler about scheduling and rotas. " * 14,
        ]
        if i == deep_at:
            parts += ["", DEEP]
    return "\n".join(parts) + "\n"


# ── short pages are untouched ──────────────────────────────────────────────────


def test_a_page_of_2000_characters_or_less_is_returned_whole():
    body = "# Short\n\n" + "x" * 1900
    assert len(body) <= passages.WHOLE_BELOW
    assert passages.best_passages(body, "anything at all") == body


def test_a_page_of_exactly_the_limit_is_whole():
    body = "y" * passages.WHOLE_BELOW
    assert passages.best_passages(body, "q") == body


def test_a_long_page_that_splits_into_no_more_than_two_passages_stays_whole():
    body = "a" * 1000 + "\n\n" + "b" * 1000  # over the limit, but two passages
    assert len(body) > passages.WHOLE_BELOW
    assert len(passages.split_passages(body)) == 2
    assert passages.best_passages(body, "anything") == body


def test_short_pages_reach_the_reference_byte_for_byte_with_front_matter_removed(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("---\ntitle: A\n---\n# A\n\nAlpha text.\n")
    b.write_text("# B\n\nBeta text.\n")
    expected = "\n\n---\n\n".join(strip_frontmatter(p.read_text()) for p in (a, b))
    assert passages.build_reference([a, b], "alpha or beta?") == expected


# ── long pages are cut to the best passages ────────────────────────────────────


def test_a_fact_deep_in_a_long_page_reaches_the_reference_when_the_question_asks_for_it():
    body = _long_page()
    out = passages.best_passages(body, "What is the rollback token and when does it expire?")
    assert DEEP in out
    assert len(out) < len(body) / 3


def test_the_title_is_kept_and_the_kept_passages_stay_in_page_order():
    body = _long_page()
    out = passages.best_passages(body, "rollback token expire")
    assert out.startswith("# Operations Manual")
    assert out.count(passages.OMITTED) >= 1
    # two questions that match two different sections keep both, earlier one first
    body2 = "# Doc\n\n" + "\n\n".join(
        [f"## Part {i}\n\n" + ("Filler words about rotas. " * 30) for i in range(12)]
    )
    body2 = body2.replace(
        "Part 3\n\n", "Part 3\n\nThe zebra protocol governs night shifts. "
    ).replace("Part 9\n\n", "Part 9\n\nThe giraffe protocol governs day shifts. ")
    out2 = passages.best_passages(body2, "zebra giraffe protocol")
    assert out2.index("zebra") < out2.index("giraffe")


def test_only_the_best_two_passages_are_kept_by_default():
    body = _long_page()
    out = passages.best_passages(body, "rollback token")
    pieces = out.split(f"\n\n{passages.OMITTED}\n\n")
    assert len(pieces) <= 3  # the title, then at most two passages
    assert all(len(piece) < passages.PASSAGE_CHARS + 200 for piece in pieces)
    three = passages.best_passages(body, "rollback token", keep=3).split(
        f"\n\n{passages.OMITTED}\n\n"
    )
    assert len(three) <= 4


def test_with_no_word_in_common_the_start_of_the_page_is_kept():
    body = _long_page()
    out = passages.best_passages(body, "zzzzqqqq xxxxyyyy")
    assert "Opening summary of the manual." in out
    assert len(out) < len(body)


def test_a_question_in_another_alphabet_still_ranks_by_its_words():
    fact = "Запись о резервном ключе: ключ=7. "  # noqa: RUF001 - Cyrillic on purpose
    question = "что такое резервном ключе"
    body = _long_page(sections=30).replace("Section 7\n\n", "Section 7\n\n" + fact)
    out = passages.best_passages(body, question)
    assert fact.strip() in out


def test_ranking_is_deterministic():
    body = _long_page()
    assert passages.best_passages(body, "rollback token") == passages.best_passages(
        body, "rollback token"
    )


# ── how a page is split ────────────────────────────────────────────────────────


def test_a_fenced_code_block_is_not_split_at_a_blank_line():
    code = "```python\nstep_one()\n\nstep_two()\n\nstep_three()\n```"
    body = (
        "# Guide\n\n"
        + ("Intro sentence about the guide. " * 20 + "\n\n")
        + code
        + "\n\n"
        + ("Tail text. " * 80)
    )
    pieces = passages.split_passages(body)
    holder = [p for p in pieces if "step_one()" in p]
    assert holder and all(s in holder[0] for s in ("step_two()", "step_three()"))


def test_a_passage_without_its_heading_gets_the_nearest_heading_above_it():
    body = (
        "# Title\n\n## Billing\n\n"
        + ("Invoices are monthly. " * 60)
        + "\n\n"
        + ("Refunds take a week. " * 60)
    )
    pieces = passages.split_passages(body)
    assert len(pieces) >= 2
    assert all("## Billing" in p for p in pieces if "Refunds" in p)


def test_one_huge_paragraph_is_split_into_passages():
    body = "# Log\n\n" + "\n".join(f"line {i} " + "z" * 60 for i in range(200))
    pieces = passages.split_passages(body)
    assert len(pieces) > 5
    assert all(len(p) <= passages.PASSAGE_CHARS + 60 for p in pieces)


def test_an_empty_body_and_a_page_with_no_title_are_handled():
    assert passages.best_passages("", "q") == ""
    body = "\n\n".join(["Section text about rotas. " * 30 for _ in range(10)])
    assert passages.best_passages(body, "rotas")  # no title line to keep, no error


# ── the real answer paths ──────────────────────────────────────────────────────


class _Backend:
    def __init__(self):
        self.sent = []

    def chat(self, messages, **k):
        self.sent = list(messages)
        return "The token is MAPLE-9021."

    def chat_stream(self, messages, **k):
        self.sent = list(messages)
        yield from ["MAPLE-", "9021"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from anthill.cache import embedder as emb

    def _boom(text):
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(emb, "embed", _boom)
    monkeypatch.setattr(emb, "available", lambda: False)

    class _Cache:
        def __init__(self, **k):
            pass

        def store(self, *a, **k):
            pass

        def lookup(self, q):
            return None

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("Operations", _long_page())
    ws.write_page("Contacts", "# Contacts\n\nOperations desk: ext 4410.\n")
    return ws


def _reference(messages):
    return next(m.content for m in messages if m.content.startswith("REFERENCE MATERIAL"))


QUESTION = "What is the rollback token for operations?"


def test_ask_sends_the_cut_page_with_the_deep_fact_and_reports_the_same_slugs(workspace):
    be = _Backend()
    _answer, slugs, _hit = ask_mod.ask(workspace, QUESTION, be)
    ref = _reference(be.sent)
    assert DEEP in ref
    page = next(p for p in workspace.pages() if p.stem == "operations")
    whole = strip_frontmatter(page.read_text())
    assert len(ref) < len(whole) / 2
    assert "operations" in slugs and "contacts" in slugs  # the pages chosen are unchanged
    assert "Operations desk: ext 4410." in ref  # a short page still goes in whole


def test_ask_stream_uses_the_same_helper(workspace):
    be = _Backend()
    out = "".join(ask_mod.ask_stream(workspace, QUESTION, be))
    assert out == "MAPLE-9021"
    ref = _reference(be.sent)
    assert DEEP in ref
    assert passages.OMITTED in ref


# ── review round ───────────────────────────────────────────────────────────────


def test_a_long_fenced_code_block_is_cut_into_pieces_that_each_keep_their_fences():
    code = "```python\n" + "\n".join(f"# step {i}\nrun_{i}()" for i in range(250)) + "\n```"
    body = "# Runbook\n\n" + ("Intro sentence for the runbook. " * 10) + "\n\n" + code
    pieces = [p for p in passages.split_passages(body) if "run_" in p]
    assert len(pieces) > 3
    for piece in pieces:
        assert piece.count("```") % 2 == 0  # no fence is left open
    # a "#" comment inside the code is not taken for a heading to carry onto the next passage
    assert not any(p.startswith("# step") for p in passages.split_passages(body)[2:])


def test_two_letter_terms_such_as_hr_and_qa_rank_passages():
    body = _long_page(sections=30).replace(
        "Section 11\n\n", "Section 11\n\nThe HR portal and the QA sign-off live in one place. "
    )
    out = passages.best_passages(body, "where is the HR portal")
    assert "HR portal" in out


def test_a_cjk_question_ranks_by_pairs_of_characters():
    fact = "回滚令牌是 MAPLE-9021，九天后过期。"  # noqa: RUF001 - Chinese on purpose
    body = _long_page(sections=30).replace("Section 7\n\n", "Section 7\n\n" + fact + " ")
    out = passages.best_passages(body, "回滚令牌是什么")
    assert "MAPLE-9021" in out
    # and the terms are pairs, so a one-character overlap does not decide it
    assert passages._words("回滚令牌") == ["回滚", "滚令", "令牌"]


def test_passages_that_were_neighbours_in_the_page_are_not_split_by_the_marker():
    body = "# Doc\n\n" + "\n\n".join(
        [f"## Part {i}\n\n" + ("Filler words about rotas. " * 25) for i in range(10)]
    )
    body = body.replace(
        "Part 4\n\n", "Part 4\n\nThe zebra protocol governs night shifts. "
    ).replace("Part 5\n\n", "Part 5\n\nThe zebra protocol also covers handovers. ")
    out = passages.best_passages(body, "zebra protocol", keep=2)
    assert "night shifts" in out and "handovers" in out
    middle = out[out.index("night shifts") : out.index("handovers")]
    assert passages.OMITTED not in middle
