"""The answer path builds its reference from typed blocks that each name their item, and the record of what the prompt held is made
from the same blocks (build plan step 2.1, slice a). The text the model is given is what it was; ids are not written into it."""

import json

import pytest

import anthill.wiki.ask as ask_mod
from anthill.wiki import answer_record, prompt_blocks
from anthill.wiki.doc_context import document_passages, format_block, summary_labels
from anthill.wiki.passages import PAGE_SEPARATOR, build_reference
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import (  # noqa: F401
    MILL,
    PRICE,
    RAW,
    SUMMARY,
    _Backend,
    _wiki,
    embeds,
)

pytestmark = pytest.mark.usefixtures("embeds")


def _old_reference(ws, question):
    """The reference text as it was made before blocks: the document passages, then the pages."""
    pages = ws.retrievable_pages()
    hits = document_passages([ws], question)
    ref = build_reference(pages, question, summary_labels(pages, hits)) if pages else ""
    return (
        (format_block(hits) + (PAGE_SEPARATOR + ref if ref else "")) if hits else ref,
        hits,
        pages,
    )


@pytest.mark.parametrize("question", [MILL, PRICE, "Hello, how are you today?"])
def test_the_reference_text_is_what_it_was_and_the_record_lists_the_same_items_as_before(
    tmp_path, question
):
    ws = _wiki(tmp_path)
    ws.write_page("gardening", "# Gardening\n\nTomatoes need sun and a weekly watering.\n")
    old, hits, pages = _old_reference(ws, question)
    new, new_hits, blocks = ask_mod._reference([ws], pages, question)
    assert new == old and [h.doc for h in new_hits] == [h.doc for h in hits]  # byte for byte
    record = prompt_blocks.record_of(blocks)
    assert (
        record["items"] == answer_record.build(pages, hits, [ws])["items"]
    )  # the panel is unchanged
    assert all(i["id"] for i in record["items"])  # every item names its record


def test_a_document_is_one_item_with_the_pages_its_passages_came_from(tmp_path):
    ws = _wiki(tmp_path)
    _text, _hits, blocks = ask_mod._reference([ws], ws.retrievable_pages(), MILL)
    passages = [b for b in blocks if b.kind == "document_passage"]
    assert len(passages) == 2 and all(b.item["name"] == RAW for b in passages)
    item = next(i for i in prompt_blocks.record_of(blocks)["items"] if i["kind"] == "document")
    assert item["pages"] == [1, 2] and item["id"] == passages[0].item["id"]


def test_a_knowledge_block_without_an_item_id_is_refused_and_does_not_reach_the_model(
    tmp_path, caplog, monkeypatch
):
    with pytest.raises(prompt_blocks.MissingItemId):
        prompt_blocks.Block("page", "text", None)
    with pytest.raises(prompt_blocks.MissingItemId):
        prompt_blocks.Block("document_passage", "text", {"id": ""})
    ws = _wiki(tmp_path)
    real = answer_record._document_item
    monkeypatch.setattr(
        answer_record, "_document_item", lambda *a, **k: {**real(*a, **k), "id": ""}
    )
    monkeypatch.setattr(answer_record, "_page_item", lambda *a, **k: {"kind": "page"})
    with caplog.at_level("WARNING"):
        text, _hits, blocks = ask_mod._reference([ws], ws.retrievable_pages(), MILL)
    assert (
        blocks == [] and text == "" and "no item id" in caplog.text
    )  # neither is given, and it is said why
    backend = _Backend()
    list(ask_mod.ask_stream(ws, MILL, backend))
    assert "Brandt mill" not in backend.prompts[0] and "Flurf" not in backend.prompts[0]


def _recorded(ws, question, *, stream=True, **kw):
    box, backend = [], _Backend()
    if stream:
        list(ask_mod.ask_stream(ws, question, backend, on_record=box.append, **kw))
    else:
        ask_mod.ask(ws, question, backend, on_record=box.append, **kw)
    assert len(box) == 1
    return box[0], backend.prompts[0]


@pytest.mark.parametrize("stream", [True, False])
def test_the_record_names_exactly_what_the_prompt_held_on_the_answer_path(tmp_path, stream):
    ws = _wiki(tmp_path)
    record, prompt = _recorded(ws, MILL, stream=stream)
    assert {i["kind"] for i in record["items"]} == {"page", "document"}
    assert (
        "Brandt mill" in prompt and "Flurf" in prompt
    )  # the document passage and the page summary were both given
    assert all(i["id"] for i in record["items"])
    unrelated = _recorded(ws, "Hello, how are you today?", stream=stream)[0]
    assert unrelated["items"] == []  # nothing was given, so nothing is listed


@pytest.mark.parametrize("stream", [True, False])
def test_the_rules_profile_memory_and_earlier_turns_are_recorded_by_type_hash_and_length_never_text(
    tmp_path, stream
):
    ws = _wiki(tmp_path)
    history = [("user", "what do we sell?"), ("assistant", "Bread and cakes.")]
    record, _ = _recorded(
        ws,
        MILL,
        stream=stream,
        history=history,
        principles="Answer in plain words.",
        profile="Name: Sam.",
        memory_context="Sam uses a deck oven.",
    )
    kinds = [b["type"] for b in record["blocks"]]
    assert kinds == ["rules", "profile", "memory", "history_turn", "history_turn"]
    assert [b.get("role") for b in record["blocks"] if b["type"] == "history_turn"] == [
        "user",
        "assistant",
    ]
    assert all(
        set(b) <= {"type", "role", "hash", "length"} and len(b["hash"]) == 16 and b["length"] > 0
        for b in record["blocks"]
    )
    stored = answer_record.dumps(record)
    for secret in ("plain words", "Name: Sam", "deck oven", "what do we sell", "Bread and cakes"):
        assert secret not in stored
    assert (
        answer_record.loads(stored)["blocks"] == record["blocks"]
    )  # kept, and the panel that reads items is unaffected
    assert (
        "blocks" not in _recorded(ws, MILL, stream=stream)[0]
    )  # no rules, profile, memory or history: nothing to list


def test_the_blocks_are_not_shown_to_the_model_as_ids(tmp_path):
    ws = _wiki(tmp_path)
    record, prompt = _recorded(ws, MILL)
    for item in record["items"]:
        assert item["id"] not in prompt  # ids live in the structure, not in the prompt text


def test_the_record_can_still_be_stored_and_shown(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page("a", "# A\n\nThe quokka sanctuary opens at dawn.\n")
    record, _ = _recorded(ws, "When does the quokka sanctuary open at dawn?")
    stored = answer_record.dumps(record)
    assert (
        json.loads(stored)["items"][0]["slug"] == "a"
        and answer_record.loads(stored)["items"] == record["items"]
    )
