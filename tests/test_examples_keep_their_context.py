"""A training example keeps the items its answer was given, and is recorded whatever the automatic-memory switch says. Model-free."""

import json

import pytest

import anthill.web.app as app_mod
from anthill.web.db import TrainingExample, User
from anthill.wiki import answer_record
from tests.test_answers_use_document_text import MILL, PRICE, chat, embeds  # noqa: F401

pytestmark = pytest.mark.usefixtures("embeds")


def _examples():
    s = app_mod._SessionFactory()
    try:
        return (
            s.query(TrainingExample)
            .filter(TrainingExample.source == "chat")
            .order_by(TrainingExample.id)
            .all()
        )
    finally:
        s.close()


def _set_auto_memory_off(off: bool):
    s = app_mod._SessionFactory()
    try:
        for u in s.query(User).all():
            u.auto_memory_off = off
        s.commit()
    finally:
        s.close()


def test_an_example_holds_the_ids_and_versions_of_the_items_its_answer_was_given(chat):  # noqa: F811
    conv = chat.ask(MILL)
    (ex,) = _examples()
    ctx = json.loads(ex.context)
    saved = json.loads(chat.row(conv).manifest)
    assert ctx["v"] == 1 and ctx["space"] == "personal" and ex.scope == "personal"
    assert [i["kind"] for i in ctx["items"]] == [i["kind"] for i in saved["items"]]
    assert {i["kind"] for i in ctx["items"]} == {
        "page",
        "document",
    }  # a page and a document were both given
    for got, want in zip(ctx["items"], saved["items"], strict=True):
        assert (
            got["id"] == want["id"]
            and got["version"] == want["version"]
            and got["space"] == want["space"]
        )
    assert (
        "Brandt" not in ex.context and "Flurf" not in ex.context
    )  # ids and versions, never the text


def test_an_example_is_recorded_whether_automatic_memory_is_on_or_paused(chat):  # noqa: F811
    chat.ask(MILL)
    assert len(_examples()) == 1
    _set_auto_memory_off(True)
    chat.ask(PRICE)
    assert len(_examples()) == 2  # pausing memory no longer stops the example being kept
    _set_auto_memory_off(False)
    chat.ask(MILL)
    assert len(_examples()) == 3
    assert {e.scope for e in _examples()} == {"personal"}  # kept privately, on this device


def test_a_failed_turn_records_no_example(chat):  # noqa: F811
    chat.state["fail"] = True
    chat.ask(PRICE)
    assert _examples() == []


def test_a_question_that_used_nothing_keeps_an_example_with_no_context(chat):  # noqa: F811
    chat.ask("Hello, how are you today?")
    (ex,) = _examples()
    assert ex.context == "" and ex.scope == "personal"


def test_the_narrowest_space_of_what_an_answer_was_given():
    def rec(*spaces):
        return {
            "items": [
                {"kind": "page", "id": str(n), "version": 1, "space": s}
                for n, s in enumerate(spaces)
            ]
        }

    assert (
        answer_record.narrowest_space(rec("personal", "team")) == "personal"
    )  # a private and a group page: private
    assert answer_record.narrowest_space(rec("team", "org")) == "team"
    assert answer_record.narrowest_space(rec("org")) == "org"
    assert answer_record.narrowest_space(rec()) == "personal"  # nothing given: private
    ctx = json.loads(answer_record.training_context(rec("personal", "team")))
    assert ctx["space"] == "personal" and [i["id"] for i in ctx["items"]] == ["0", "1"]
    assert answer_record.training_context(rec()) == ""


def test_the_memory_page_says_the_switch_is_only_about_remembering_facts(chat):  # noqa: F811
    from bs4 import BeautifulSoup

    text = BeautifulSoup(chat.client.get("/memory").text, "html.parser").find(id="training-note")
    assert text is not None and "only about remembering facts" in text.get_text()
    assert "kept on this device, privately" in " ".join(text.get_text().split())
