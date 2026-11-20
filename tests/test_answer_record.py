"""Each answer records which items it was given, and the chat shows them (#281). Model-free: the model is a fake
and embeddings are a word-hash vector."""

import json

import pytest
from bs4 import BeautifulSoup

import anthill.web.app as app_mod
import anthill.wiki.ask as ask_mod
from anthill.lifecycle.version import tag_page
from anthill.web import answer_panel
from anthill.web.db import ChatMessage
from anthill.wiki import answer_record, items
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import (  # noqa: F401
    HANDBOOK,
    MILL,
    PRICE,
    RAW,
    SUMMARY,
    _Backend,
    chat,
    embeds,
)

PAGE = "hollis-bakery-operations-handbook"
OTHER = "# Gardening\n\nTomatoes need sun and a weekly watering. Nothing else is written here.\n"


def _world(root, *, scope="personal", raw=RAW, summary=SUMMARY, name=PAGE) -> Workspace:
    """A wiki with an original (kept bytes, text, record) and the page a model wrote from it, plus a page about
    something else."""
    from anthill.wiki import doc_index

    ws = Workspace(root, scope=scope)
    ws.init()
    ws.text.mkdir(exist_ok=True)
    (ws.raw / raw).write_bytes(b"%PDF-1.4 " + raw.encode())
    (ws.text / f"{raw}.txt").write_text(HANDBOOK, encoding="utf-8")
    items.ensure_source_record(ws, ws.raw / raw)
    ws.write_page(name, tag_page(summary, "qwen2.5:3b", on="2026-11-18", source=raw))
    ws.write_page("gardening", OTHER)
    doc_index.sync_workspace(ws.root)
    return ws


def _turn(ws, question, *, stream=True, extra=None):
    """(record, prompt) of one answer, from the real answer code."""
    box: list = []
    backend = _Backend()
    if stream:
        list(
            ask_mod.ask_stream(ws, question, backend, on_record=box.append, extra_workspaces=extra)
        )
    else:
        ask_mod.ask(ws, question, backend, on_record=box.append, extra_workspaces=extra)
    assert len(box) == 1
    return box[0], backend.prompts[0]


def _by_kind(record):
    return {i["kind"]: i for i in record["items"]}


@pytest.mark.parametrize("stream", [True, False])
def test_the_record_holds_every_field_of_the_page_and_the_document(tmp_path, embeds, stream):  # noqa: F811
    ws = _world(tmp_path / "w")
    record, _prompt = _turn(ws, MILL, stream=stream)
    got = _by_kind(record)
    page_rec = items.read_page_record((ws.wiki / f"{PAGE}.md").read_text())
    src_rec = items.read_source_record(ws, RAW)
    assert got["page"] == {
        "kind": "page",
        "slug": PAGE,
        "title": "Hollis Bakery: Operations Handbook",
        "space": "personal",
        "space_ref": "",
        "id": page_rec.id,
        "version": 1,
        "hash": page_rec.content_hash,
        "origin": "model-written",
        "review": "unchecked",
    }
    assert got["document"] == {
        "kind": "document",
        "name": RAW,
        "title": "hollis-bakery-handbook.pdf",
        "pages": [1, 2],
        "space": "personal",
        "space_ref": "",
        "id": src_rec.id,
        "version": 1,
        "hash": src_rec.content_hash,
        "origin": "own",
        "review": "checked",
    }
    assert record["more"] == 0 and record["v"] == 1


@pytest.mark.parametrize("stream", [True, False])
def test_the_record_is_the_set_of_items_in_the_prompt(tmp_path, embeds, stream):  # noqa: F811
    ws = _world(tmp_path / "w")
    record, prompt = _turn(ws, MILL, stream=stream)
    assert (
        "Brandt mill" in prompt and "Flurf" in prompt
    )  # the document passage and the page summary
    assert {i["kind"] for i in record["items"]} == {"document", "page"}
    assert [i["slug"] for i in record["items"] if i["kind"] == "page"] == [PAGE]
    assert "Tomatoes" not in prompt  # the unrelated page was not given ...
    assert "gardening" not in json.dumps(record)  # ... and is not recorded


def test_a_question_that_used_nothing_records_nothing(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    record, _ = _turn(ws, "Hello, how are you today?")
    assert record["items"] == [] and answer_record.dumps(record) == ""


@pytest.mark.parametrize("stream", [True, False])
def test_recording_does_not_change_the_prompt(tmp_path, embeds, stream):  # noqa: F811
    ws = _world(tmp_path / "w")
    _, with_record = _turn(ws, MILL, stream=stream)
    backend = _Backend()
    if stream:
        list(ask_mod.ask_stream(ws, MILL, backend))
    else:
        ask_mod.ask(ws, MILL, backend)
    assert backend.prompts[0] == with_record


def test_the_record_holds_no_page_or_passage_text(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    record, _ = _turn(ws, MILL)
    text = answer_record.dumps(record)
    for words in ("Brandt mill", "Flurf", "founded in 2011", "Wiesenhof", "Tuesday and Friday"):
        assert words not in text


def test_an_item_with_no_record_is_not_given_to_the_model_so_it_is_not_recorded(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    items.ensure_records(ws.root)  # the wiki is already prepared; what is made now has no record
    (ws.root / "records" / "sources" / f"{RAW}.json").unlink()  # an original with no record
    (ws.wiki / f"{PAGE}.md").write_text(SUMMARY)  # a page with no record either
    record, prompt = _turn(ws, MILL)
    assert record["items"] == []
    assert "Brandt" not in prompt and "Flurf" not in prompt  # neither was given


def test_the_record_lists_a_page_without_a_record_with_an_empty_id_when_it_is_asked_to(tmp_path):
    ws = _world(tmp_path / "w")
    (ws.wiki / f"{PAGE}.md").write_text(SUMMARY)  # no record
    record = answer_record.build([ws.wiki / f"{PAGE}.md"], [], [ws])
    assert record["items"][0]["id"] == "" and record["items"][0]["version"] == 0
    assert answer_panel.panel(answer_record.dumps(record))["rows"][0]["version"] == 0


def test_a_damaged_page_record_still_lists_the_page(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    (ws.wiki / f"{PAGE}.md").write_text("---\nx-anthill-item: [not, a, record\n---\n" + SUMMARY)
    page = ws.wiki / f"{PAGE}.md"
    record = answer_record.build([page], [], [ws])
    assert record["items"][0]["slug"] == PAGE and record["items"][0]["id"] == ""


def test_a_record_names_at_most_fifty_items_and_says_how_many_more(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    for n in range(60):
        ws.write_page(f"page-{n:02d}", f"# Page {n}\n\nText {n}.\n")
    pages = sorted(ws.wiki.glob("page-*.md"))
    record = answer_record.build(pages, [], [ws])
    assert len(record["items"]) == 50 and record["more"] == 10
    shown = answer_panel.panel(answer_record.dumps(record))
    assert len(shown["rows"]) == 50 and shown["more"] == 10


def test_two_pages_with_one_name_in_two_spaces_are_told_apart(tmp_path, embeds):  # noqa: F811
    mine = _world(tmp_path / "user-1", scope="personal", raw="mine-aaaaaaaaaaaa.pdf")
    org = _world(tmp_path / "org-1", scope="org", raw="theirs-bbbbbbbbbbbb.pdf")
    record, _prompt = _turn(mine, MILL, extra=[org])
    pages = [i for i in record["items"] if i["kind"] == "page"]
    assert [(p["slug"], p["space"], p["space_ref"]) for p in pages] == [
        (PAGE, "personal", "user-1"),
        (PAGE, "org", "org-1"),
    ]
    assert pages[0]["id"] != pages[1]["id"]
    rows = answer_panel.panel(answer_record.dumps(record))["rows"]
    hrefs = {r["href"] for r in rows if not r["document"]}
    assert hrefs == {f"/wiki/page/{PAGE}?scope=personal", f"/wiki/page/{PAGE}?scope=org"}


def test_a_cached_answer_is_recorded_from_its_sources(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    sources = [PAGE, "doc:" + RAW + " (page 1, 2)", "no-such-page", "doc:gone-cccccccccccc.pdf"]
    record = answer_record.from_sources(sources, [ws])
    got = _by_kind(record)
    assert got["page"]["slug"] == PAGE and got["document"]["pages"] == [1, 2]
    assert len(record["items"]) == 2  # what is no longer there is left out


def test_the_unchecked_label_follows_the_review_state(tmp_path, embeds):  # noqa: F811
    ws = _world(tmp_path / "w")
    ws.write_page(
        "approved",
        tag_page("# Approved\n\nA checked page.\n", "qwen2.5:3b", on="2026-11-18"),
        review="approved",
    )
    ws.write_page("mine", "# Mine\n\nWritten by a person.\n")
    pages = [ws.wiki / f"{s}.md" for s in (PAGE, "approved", "mine")]
    rows = answer_panel.panel(answer_record.dumps(answer_record.build(pages, [], [ws])))["rows"]
    assert {r["title"]: r["unchecked"] for r in rows} == {
        "Hollis Bakery: Operations Handbook": True,
        "Approved": False,
        "Mine": False,
    }


def test_a_stored_record_that_cannot_be_read_is_an_empty_one():
    for bad in ("", "{not json", "[]", '{"items": "x"}', "null"):
        assert answer_record.loads(bad)["items"] == []
    assert answer_panel.panel("{not json") == {"rows": [], "more": 0}


# ── through the chat ──────────────────────────────────────────────────────────


def _meta(html):
    soup = BeautifulSoup(html, "html.parser")
    parts = [
        n.get_text()
        for n in soup.select("div.msg.assistant .meta, div.msg.assistant .answer-record")
    ]
    return " ".join(" ".join(parts).split())


def test_the_chat_keeps_the_record_and_shows_the_panel_after_a_reload(chat):  # noqa: F811
    conv = chat.ask(MILL)
    saved = chat.row(conv)
    record = json.loads(saved.manifest)
    assert {i["kind"] for i in record["items"]} == {"page", "document"}
    soup = BeautifulSoup(chat.client.get(f"/chat/{conv}").text, "html.parser")
    panel = soup.select_one("div.msg.assistant details.answer-record")
    assert panel and "What this answer was given (2)" in panel.get_text(" ", strip=True)
    links = {a.get_text(strip=True): a["href"] for a in panel.find_all("a")}
    assert links["Hollis Bakery: Operations Handbook"] == f"/wiki/page/{PAGE}?scope=personal"
    assert links["hollis-bakery-handbook.pdf"] == f"/wiki/text/{RAW}?scope=personal"
    text = panel.get_text(" ", strip=True)
    assert "page 1, 2" in text and "version 1" in text and "AI-written, unchecked" in text
    assert "Pages in context" not in _meta(chat.client.get(f"/chat/{conv}").text)  # replaced
    assert "caused" not in text and "cited" not in text


def test_the_page_lists_what_was_used_in_the_whole_chat(chat):  # noqa: F811
    conv = chat.ask(MILL)
    html = chat.client.get(f"/chat/{conv}").text
    box = BeautifulSoup(html, "html.parser").select_one("details.used-in-chat")
    assert box and "Used in this chat (2)" in box.get_text(" ", strip=True)
    assert "1 answer" in box.get_text(" ", strip=True)


def test_used_in_chat_counts_each_item_once_with_how_many_answers_used_it():
    class M:
        def __init__(self, role, manifest):
            self.role, self.manifest = role, manifest

    rec = answer_record.dumps(
        {
            "v": 1,
            "more": 0,
            "items": [
                {"kind": "page", "slug": "a", "title": "A", "space": "personal", "space_ref": ""},
                {"kind": "page", "slug": "a", "title": "A", "space": "org", "space_ref": "org-1"},
            ],
        }
    )
    got = answer_panel.used_in_chat([M("assistant", rec), M("user", ""), M("assistant", rec)])
    assert [(r["space"], r["answers"]) for r in got] == [("Your wiki", 2), ("Organisation wiki", 2)]


def test_the_live_page_gets_the_panel_and_drops_the_two_plain_lists(chat):  # noqa: F811
    conv = chat.ask(MILL)
    stream = chat.client.get(f"/chat/{conv}/stream", params={"message": MILL}).text
    metas = [
        json.loads(line[6:])["meta"]
        for line in stream.splitlines()
        if line.startswith("data: {") and '"meta"' in line
    ]
    html = next(m["record_html"] for m in metas if "record_html" in m)
    assert "What this answer was given (2)" in html and f"/wiki/page/{PAGE}?scope=personal" in html
    assert any("wiki_slugs" in m for m in metas)  # the plain events are still sent to other clients
    page = chat.client.get(f"/chat/{conv}").text
    assert "d.meta?.record_html" in page and "legacyMeta" in page


def test_an_old_answer_keeps_its_plain_lists(chat):  # noqa: F811
    conv = chat.ask(MILL)
    sess = app_mod._SessionFactory()
    try:
        msg = (
            sess.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conv, ChatMessage.role == "assistant")
            .one()
        )
        msg.manifest = ""  # made before the record existed
        sess.commit()
    finally:
        sess.close()
    html = chat.client.get(f"/chat/{conv}").text
    shown = _meta(html)
    assert "Documents in context: hollis-bakery-handbook.pdf (page 1, 2)" in shown
    assert "Pages in context: hollis-bakery-operations-handbook" in shown
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one("div.msg.assistant details.answer-record") is None
    assert soup.select_one("details.used-in-chat") is None


def test_a_question_nothing_answers_and_a_failed_turn_save_no_record(chat):  # noqa: F811
    conv = chat.ask("Hello, how are you today?")
    assert chat.row(conv).manifest == ""
    chat.state["fail"] = True
    failed = chat.ask(PRICE)
    assert chat.row(failed).generation_failed is True and chat.row(failed).manifest == ""
    assert "What this answer was given" not in chat.client.get(f"/chat/{failed}").text


def test_the_page_after_a_reload_is_the_same_as_the_live_panel(chat):  # noqa: F811
    conv = chat.ask(MILL)
    stream = chat.client.get(f"/chat/{conv}/stream", params={"message": MILL}).text
    live = next(
        json.loads(line[6:])["meta"]["record_html"]
        for line in stream.splitlines()
        if line.startswith("data: {") and "record_html" in line
    )
    reloaded = BeautifulSoup(chat.client.get(f"/chat/{conv}").text, "html.parser").select_one(
        "div.msg.assistant details.answer-record"
    )
    assert " ".join(BeautifulSoup(live, "html.parser").get_text().split()) == " ".join(
        reloaded.get_text().split()
    )
