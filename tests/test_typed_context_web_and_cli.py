"""The web-search answer and the document command build their prompts from typed blocks (build plan step 2.1, slice c).
Web results are untrusted evidence with an address and a fetch time; the pages the document command reads each name their item.
What the model is given is what it was."""

import pytest
from typer.testing import CliRunner

import anthill.cli as cli
from anthill.search import web as web_mod
from anthill.search.web import SearchResult
from anthill.wiki import answer_record, prompt_blocks
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace
from tests.test_web_turn_stream import _Backend, _fake_web, _no_cache, _two_results, _workspace

GARDEN = "# Gardening\n\nTomatoes need sun. The Flurf trowel is kept in the shed.\n"


def _ask_web(ws, be, **kw):
    box: list = []
    list(
        ask_mod.ask_stream(
            ws,
            "What is the capital of Iceland?",
            be,
            web_search=True,
            search_query="capital of Iceland",
            on_record=box.append,
            **kw,
        )
    )
    return box


def test_the_web_part_of_an_answer_is_recorded_as_untrusted_evidence_by_address_hash_and_length_never_text(
    tmp_path, monkeypatch
):
    ws = _workspace(tmp_path, monkeypatch)
    _fake_web(monkeypatch, _two_results())
    _no_cache(monkeypatch)
    be = _Backend()
    box = _ask_web(ws, be, principles="Answer in plain words.")
    assert len(box) == 2 and not [
        b for b in box[0].get("blocks", []) if b["type"] == "web_evidence"
    ]  # before the web part: wiki only
    rec = box[-1]
    web = [b for b in rec["blocks"] if b["type"] == "web_evidence"]
    assert [b["url"] for b in web] == [
        "https://example.org/reykjavik",
        "https://example.org/iceland",
    ]
    assert all(b["fetched"].endswith("Z") and len(b["hash"]) == 16 and b["length"] > 0 for b in web)
    assert [i["slug"] for i in rec["items"]] == [
        "iceland"
    ]  # the wiki page is an item; web evidence is not
    stored = answer_record.dumps(rec)
    assert "Reykjavik is the capital" not in stored and "plain words" not in stored
    assert answer_record.loads(stored)["blocks"] == rec["blocks"]
    prompt = "\n".join(m.content for m in be.seen["messages"])
    assert (
        "Reykjavik is the capital" in prompt and "https://example.org/iceland" in prompt
    )  # what the model was given


def test_only_the_web_results_the_prompt_holds_are_blocks():
    results = [*_two_results(), SearchResult("Empty", "https://example.org/empty", "")]
    blocks = prompt_blocks.web_blocks(results)
    assert [b.item["url"] for b in blocks] == [
        "https://example.org/reykjavik",
        "https://example.org/iceland",
    ]
    assert all(b.kind == "web_evidence" and "Reykjavik" in b.text for b in blocks[:1])


def test_a_web_turn_where_the_search_fails_keeps_the_wiki_record_and_lists_no_web(
    tmp_path, monkeypatch
):
    ws = _workspace(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(web_mod, "web_search", boom)
    _no_cache(monkeypatch)
    box = _ask_web(ws, _Backend())
    assert box and not [b for b in box[-1].get("blocks", []) if b["type"] == "web_evidence"]


def test_web_evidence_needs_no_item_but_a_page_still_does():
    block = prompt_blocks.Block(
        "web_evidence", "text", {"url": "https://x.org", "fetched": "2026-11-25T00:00:00Z"}
    )
    assert block.kind == "web_evidence"
    with pytest.raises(prompt_blocks.MissingItemId):
        prompt_blocks.Block("page", "text", {"url": "https://x.org"})


class _CliBackend:
    model = "m"

    def __init__(self):
        self.messages = None

    def chat(self, messages, **kw):
        self.messages = messages
        return "# Done\n\nA document."


def _generate(tmp_path, monkeypatch, ws):
    be = _CliBackend()
    monkeypatch.setattr(cli, "build_backend", lambda cfg: be)
    out = tmp_path / "out.md"
    r = CliRunner().invoke(
        cli.app, ["-w", str(ws.root), "generate", "write about gardening", "-o", str(out)]
    )
    assert r.exit_code == 0, r.output
    return be, r.output


def _garden(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page("gardening", GARDEN)
    ws.write_page("ovens", "# Ovens\n\nThe deck oven is heated first.\n")
    return ws


def test_the_document_command_gives_the_model_the_same_text_and_says_which_pages_it_was_built_from(
    tmp_path, monkeypatch
):
    ws = _garden(tmp_path)
    be, output = _generate(tmp_path, monkeypatch, ws)
    pages = ws.retrievable_pages()[:6]
    expected = "\n\n---\n\n".join(p.read_text() for p in pages)
    assert (
        f"WIKI CONTEXT:\n{expected}\n\nREQUEST: write about gardening" in be.messages[1].content
    )  # byte for byte
    assert "Built from: " in output and "gardening" in output and "ovens" in output


def test_a_page_that_cannot_name_its_item_is_not_given_to_the_document_command(
    tmp_path, monkeypatch
):
    ws = _garden(tmp_path)
    real = answer_record._page_item
    monkeypatch.setattr(
        answer_record,
        "_page_item",
        lambda path, w: {**real(path, w), "id": ""} if path.stem == "ovens" else real(path, w),
    )
    be, output = _generate(tmp_path, monkeypatch, ws)
    assert "Flurf" in be.messages[1].content and "deck oven" not in be.messages[1].content
    assert "gardening" in output and "ovens" not in output


def test_the_document_command_with_no_pages_still_runs_and_lists_nothing(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    be, output = _generate(tmp_path, monkeypatch, ws)
    assert "WIKI CONTEXT:\n\n" in be.messages[1].content and "Built from" not in output


def test_the_council_is_given_the_same_blocks_as_the_single_model_and_a_page_without_an_item_id_reaches_neither(
    tmp_path, monkeypatch
):
    import anthill.council.engine as council_eng
    from tests.test_ask_confidence_suggestion import (
        _cfg,
        _mock_resolved,
        _noop_decrypt,
        _use_recording_cache,
    )

    _use_recording_cache(monkeypatch)
    monkeypatch.setattr("anthill.cache.embedder.available", lambda: False)
    ws = _garden(tmp_path)
    _mock_resolved(monkeypatch, 2)
    seen: list = []
    monkeypatch.setattr(
        council_eng,
        "run_council",
        lambda cfg, messages, decrypt, **k: (
            seen.append(list(messages))
            or council_eng.CouncilResult(answer="In the shed.", synthesized=True)
        ),
    )
    box: list = []
    ask_mod.ask(
        ws,
        "Where is the Flurf trowel kept?",
        _CliBackend(),
        cfg=_cfg(),
        decrypt=_noop_decrypt,
        on_record=box.append,
    )
    assert "Flurf trowel is kept in the shed" in "\n".join(m.content for m in seen[0])
    given = "\n".join(m.content for m in seen[0])
    listed = {i["slug"] for i in box[0]["items"]}
    assert "gardening" in listed and ("deck oven" in given) == (
        "ovens" in listed
    )  # the record lists what the council was given

    real = answer_record._page_item
    monkeypatch.setattr(answer_record, "_page_item", lambda path, w: {**real(path, w), "id": ""})
    seen.clear()
    box.clear()
    ask_mod.ask(
        ws,
        "Where is the Flurf trowel kept?",
        _CliBackend(),
        cfg=_cfg(),
        decrypt=_noop_decrypt,
        on_record=box.append,
    )
    assert "kept in the shed" not in "\n".join(m.content for m in seen[0]) and box[0]["items"] == []
