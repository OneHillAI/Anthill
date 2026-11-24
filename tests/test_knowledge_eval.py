"""The knowledge evaluation set (build plan step 2.0): the cases are well formed, answers are scored without a model, and a run goes
through the real answer path and stores a result two runs can be compared on. Model-free: the model is a stub that gives scripted
answers."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))

from knowledge_eval import cases as cases_mod
from knowledge_eval import compare as compare_mod
from knowledge_eval import run as run_mod
from knowledge_eval import score as score_mod

from tests.test_answers_use_document_text import embeds  # noqa: F401


def _case(**kw):
    base = {
        "id": "x",
        "kind": "lookup",
        "question": "q?",
        "wiki": {"p": "# P\n\ntext\n"},
        "must_include": [["45 days"]],
    }
    return cases_mod._check({**base, **kw}, "test")


# ── the cases ─────────────────────────────────────────────────────────────────


def test_the_starter_set_is_well_formed_covers_every_kind_and_says_who_wrote_it():
    cases, set_hash = cases_mod.load("starter")
    assert len(set_hash) == 16 and len({c.id for c in cases}) == len(cases)
    assert {c.kind for c in cases} == set(cases_mod.KINDS)
    assert all(
        c.author == "builders" and not c.independent for c in cases
    )  # the result must not read as independent
    assert all(c.abstain for c in cases if c.kind in ("unanswerable", "deletion"))
    assert all(c.removed for c in cases if c.kind == "deletion")


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "nonsense"},
        {"must_include": []},  # neither required facts nor an abstention
        {"must_include": [[]]},
        {"removed": ["not-there"]},
    ],
)
def test_a_badly_formed_case_is_refused_with_a_reason(bad):
    with pytest.raises(cases_mod.CaseError):
        _case(**bad)


def test_a_missing_field_a_duplicate_id_and_a_missing_set_are_refused(tmp_path):
    with pytest.raises(cases_mod.CaseError, match="needs id"):
        cases_mod._check({"id": "x"}, "t")
    line = json.dumps(
        {"id": "a", "kind": "lookup", "question": "q", "wiki": {"p": "t"}, "must_include": [["t"]]}
    )
    (tmp_path / "dup.jsonl").write_text(line + "\n" + line + "\n")
    with pytest.raises(cases_mod.CaseError, match="duplicate id"):
        cases_mod.load(str(tmp_path / "dup.jsonl"))
    with pytest.raises(cases_mod.CaseError, match="no set"):
        cases_mod.load("no-such-set")


# ── the scoring ───────────────────────────────────────────────────────────────


def test_every_required_group_needs_one_alternative_and_no_forbidden_phrase_may_appear():
    c = _case(must_include=[["17:00", "5 pm"], ["15:00", "3 pm"]], must_not_include=["16:00"])
    assert score_mod.score(
        c, "It closes at 17:00 according to one memo and 3 PM according to the other."
    )["passed"]
    miss = score_mod.score(c, "It closes at 17:00.")
    assert not miss["passed"] and miss["missing"] == [["15:00", "3 pm"]]
    assert score_mod.score(c, "17:00 or 15:00, maybe 16:00.")["forbidden"] == ["16:00"]


def test_an_answer_that_should_abstain_must_say_it_is_not_known_and_must_not_give_the_fact():
    c = _case(kind="deletion", must_include=[], abstain=True, must_not_include=["4729"], removed=[])
    assert score_mod.score(c, "I don't know the door code; it is not in the knowledge base.")[
        "passed"
    ]
    assert not score_mod.score(c, "The back door code is 4729.")["passed"]
    assert not score_mod.score(c, "The code is something else entirely.")[
        "passed"
    ]  # it neither knew nor said so
    assert score_mod.abstained("There is no information about that.") and not score_mod.abstained(
        "The shop opens at 8."
    )


def test_dashes_spaces_and_case_do_not_hide_a_correct_answer():
    c = _case(must_include=[["45-day"]])
    assert score_mod.score(c, "A 45" + chr(0x2011) + "day window applies.")[
        "passed"
    ]  # a non-breaking hyphen
    assert score_mod.norm("A" + chr(0xA0) + " B" + chr(10) + " C") == "a b c"


# ── a run and a comparison ────────────────────────────────────────────────────


class _Engine:
    def version(self):
        return "0.0-test"

    def require(self, tags):
        return {t: "sha-" + t for t in tags}


class _Scripted:
    """Answers from a table keyed by the question, and records every prompt it was given."""

    model = "stub"

    def __init__(self, answers):
        self.answers, self.prompts = answers, []

    def _answer(self, messages):
        self.prompts.append("\n".join(m.content for m in messages))
        question = messages[-1].content.removeprefix("QUESTION: ")
        return self.answers.get(question, "I don't know.")

    def chat(self, messages, **k):
        return self._answer(messages)

    def chat_stream(self, messages, **k):
        yield self._answer(messages)


@pytest.mark.usefixtures("embeds")
def test_a_run_goes_through_the_real_answer_path_stores_a_comparable_result_and_never_gives_a_removed_page(
    tmp_path,
):
    cases, _ = cases_mod.load("starter")
    perfect = {
        c.question: " and ".join(g[0] for g in c.must_include) for c in cases if not c.abstain
    }
    perfect.update(
        {c.question: "I don't know, it is not in the knowledge base." for c in cases if c.abstain}
    )
    perfect[cases[0].question] = "Something wrong."  # one scripted failure
    backend = _Scripted(perfect)
    out = tmp_path / "out"
    res = run_mod.run(
        "starter", ["tiny:1b"], engine=_Engine(), backend_for=lambda tag: backend, out_dir=out
    )
    summary = res["results"]["tiny:1b"]["summary"]
    assert summary["cases"] == 14 and summary["passed"] == 13 and summary["errors"] == 0
    assert summary["by_kind"]["lookup"] == {"cases": 2, "passed": 1}
    assert (
        res["independent_cases"] == 0
        and res["models"] == {"tiny:1b": "sha-tiny:1b"}
        and res["engine"] == "0.0-test"
    )
    stored = json.loads(Path(res["_path"]).read_text())
    assert (
        stored["set_hash"] == res["set_hash"]
        and stored["results"]["tiny:1b"]["cases"][0]["first_word_ms"] is not None
    )
    assert not any(
        "4729" in p or "62,000" in p for p in backend.prompts
    )  # a removed page is never given to the model
    deletion = [r for r in stored["results"]["tiny:1b"]["cases"] if r["kind"] == "deletion"]
    assert deletion and all(
        r["pages_given"] == [p for p in r["pages_given"] if p] for r in deletion
    )
    assert not any(p in ("keycode", "salary-bands") for r in deletion for p in r["pages_given"])


@pytest.mark.usefixtures("embeds")
def test_a_model_that_is_not_installed_stops_the_run_before_anything_is_asked(tmp_path):
    class _Missing(_Engine):
        def require(self, tags):
            raise RuntimeError("Not installed: ghost:1b")

    with pytest.raises(RuntimeError, match="Not installed"):
        run_mod.run(
            "starter",
            ["ghost:1b"],
            engine=_Missing(),
            backend_for=lambda t: _Scripted({}),
            out_dir=None,
        )


def test_the_comparison_names_what_got_worse_and_warns_when_the_sets_differ():
    def result(passed, first, set_hash="s1"):
        return {
            "set_hash": set_hash,
            "scoring_version": 1,
            "results": {
                "m": {
                    "summary": {
                        "passed": passed,
                        "cases": 2,
                        "by_kind": {"lookup": {"passed": passed, "cases": 2}},
                        "first_word_ms_median": first,
                    }
                }
            },
        }

    lines = compare_mod.compare(result(2, 500), result(1, 300))
    assert any("lookup: 2/2 -> 1/2  worse" in line for line in lines) and any(
        "500 ms -> 300 ms" in line for line in lines
    )
    assert any(
        "different sets" in line
        for line in compare_mod.compare(result(2, 1), result(2, 1, set_hash="s2"))
    )
    assert not any(
        "worse" in line or "better" in line
        for line in compare_mod.compare(result(2, 1), result(2, 1))
    )


def test_ordinary_ways_of_saying_it_is_not_known_count_and_asking_which_company_does_not():
    for said in (
        "The information provided does not contain any details about that.",
        "I don't have specific salary band information for C.",
        "The reference material doesn't include it.",
    ):
        assert score_mod.abstained(said), said
    assert not score_mod.abstained(
        "To give the right answer I would need to know which company you mean."
    )
    assert not score_mod.abstained("Band C starts at 53,000.")


def test_a_stored_result_can_be_scored_again_without_a_model_and_a_changed_set_is_refused(tmp_path):
    cases, set_hash = cases_mod.load("starter")
    rows = [
        {
            "id": c.id,
            "kind": c.kind,
            "question": c.question,
            "answer": "The material does not contain that.",
            "passed": False,
            "abstained": False,
            "missing": [],
            "forbidden": [],
            "pages_given": [],
            "first_word_ms": 100,
            "total_ms": 200,
            "error": "",
        }
        for c in cases
    ]
    result = {
        "set": "starter",
        "set_hash": set_hash,
        "scoring_version": 1,
        "results": {"m": {"summary": {}, "cases": rows}},
    }
    again = run_mod.rescore(result)
    passed = {r["id"] for r in again["results"]["m"]["cases"] if r["passed"]}
    assert passed == {
        c.id for c in cases if c.abstain
    }  # an abstaining answer passes exactly the cases that want one
    assert (
        again["scoring_version"] == score_mod.SCORING_VERSION
        and again["results"]["m"]["summary"]["cases"] == 14
    )
    result["set_hash"] = "other"
    with pytest.raises(ValueError, match="set has changed"):
        run_mod.rescore(result)
