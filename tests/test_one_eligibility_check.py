"""One rule decides what may be read, on every route that reads knowledge. Each test seeds an item that may not be read (one that was
removed, one that is waiting) with a unique phrase, asks on a route, and checks the phrase never appears in what the model was given, in
an answer, in a tool result or in a search result. Model-free."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

import anthill.wiki.ask as ask_mod
from anthill.agent.tools import _read_wiki, _search_wiki
from anthill.lifecycle.version import tag_page
from anthill.web import db as db_mod
from anthill.web.db import Organization
from anthill.wiki import answer_record, doc_index, items, removal
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import _Backend, embeds  # noqa: F401

OK_DOC, GONE_DOC, HELD_DOC = "ok-aaaaaaaaaaaa.pdf", "gone-bbbbbbbbbbbb.pdf", "held-cccccccccccc.pdf"
OK_PHRASE = "The quokka sanctuary opens at dawn."
GONE_PHRASE = "The cobalt walrus guards the lantern archive."
HELD_PHRASE = "The amber llama keeps the vault key in drawer nine."
Q_GONE = "Who guards the cobalt walrus lantern archive?"
Q_HELD = "Where does the amber llama keep the vault key in drawer nine?"
Q_OK = "When does the quokka sanctuary open at dawn?"


def _world(root: Path) -> Workspace:
    """A wiki with one readable document and page, a document and page that were then removed, and a document and page that are waiting."""
    ws = Workspace(root, scope="personal")
    ws.init()
    ws.text.mkdir(exist_ok=True)
    for name, text, slug in (
        (OK_DOC, OK_PHRASE, "ok-page"),
        (GONE_DOC, GONE_PHRASE, "gone-page"),
        (HELD_DOC, HELD_PHRASE, "held-page"),
    ):
        (ws.raw / name).write_bytes(b"%PDF-1.4 " + name.encode())
        (ws.text / f"{name}.txt").write_text(text, encoding="utf-8")
        items.ensure_source_record(ws, ws.raw / name)
        ws.write_page(
            slug, tag_page(f"# {slug}\n\n{text}\n", "qwen2.5:3b", on="2026-11-01", source=name)
        )
    rec = items.read_source_record(ws, HELD_DOC)
    rec.review_state = "proposed"  # an upload that is waiting for a decision
    items.write_source_record(ws, rec)
    held_page = ws.wiki / "held-page.md"
    held_page.write_text(
        held_page.read_text().replace("review_state: unchecked", "review_state: proposed")
    )
    doc_index.sync_workspace(ws.root)
    removal.remove_original(ws, GONE_DOC)  # takes its unchecked page with it
    return ws


def _prompt_of(ws, question, *, stream=True, **kw):
    backend = _Backend()
    if stream:
        answer = "".join(
            c for c in ask_mod.ask_stream(ws, question, backend, **kw) if isinstance(c, str)
        )
    else:
        answer = ask_mod.ask(ws, question, backend, **kw)[0]
    return backend.prompts[0], answer


@pytest.mark.usefixtures("embeds")
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize(("question", "phrase"), [(Q_GONE, GONE_PHRASE), (Q_HELD, HELD_PHRASE)])
def test_chat_widget_and_slack_never_give_the_model_an_item_that_may_not_be_read(
    tmp_path, stream, question, phrase
):
    ws = _world(tmp_path / "w")
    prompt, answer = _prompt_of(ws, question, stream=stream)
    assert phrase not in prompt and phrase not in answer


@pytest.mark.usefixtures("embeds")
def test_the_control_item_is_still_read(tmp_path):
    ws = _world(tmp_path / "w")
    prompt, _ = _prompt_of(ws, Q_OK)
    assert OK_PHRASE in prompt  # the rule keeps out only what may not be read


@pytest.mark.usefixtures("embeds")
def test_an_item_in_a_wiki_the_chat_does_not_use_is_never_read(tmp_path):
    mine = _world(tmp_path / "user-1")
    other = Workspace(tmp_path / "user-2", scope="personal")
    other.init()
    other.write_page("secret-page", "# Secret\n\nThe violet heron nests under the clock tower.\n")
    prompt, answer = _prompt_of(mine, "Where does the violet heron nest under the clock tower?")
    assert "violet heron nests" not in prompt and "violet heron nests" not in answer


@pytest.mark.usefixtures("embeds")
@pytest.mark.parametrize("phrase", [GONE_PHRASE, HELD_PHRASE])
def test_the_agent_tools_cannot_find_or_read_an_item_that_may_not_be_read(tmp_path, phrase):
    ws = _world(tmp_path / "w")
    found = json.dumps(_search_wiki(phrase, str(ws.root), k=10))
    assert phrase not in found
    for slug in ("gone-page", "held-page"):
        assert phrase not in _read_wiki(slug, str(ws.root))


def test_the_command_line_document_command_reads_only_what_may_be_read(tmp_path, monkeypatch):
    from anthill import cli

    ws = _world(tmp_path / "w")
    seen: list[str] = []

    class _Fake:
        def chat(self, messages, **k):
            seen.append("\n".join(m.content for m in messages))
            return "# Summary\n\ndone\n"

    monkeypatch.setattr(cli, "build_backend", lambda cfg: _Fake())
    out = tmp_path / "out.md"
    res = CliRunner().invoke(cli.app, ["-w", str(ws.root), "generate", "summarise", "-o", str(out)])
    assert res.exit_code == 0 and seen
    assert HELD_PHRASE not in seen[0] and GONE_PHRASE not in seen[0]
    assert OK_PHRASE in seen[0]


def test_the_mcp_wiki_tool_reads_only_what_may_be_read_and_the_cache_and_memory_tools_are_not_offered(
    tmp_path, monkeypatch
):
    from anthill.mcp.server import exposed_resources, tool_specs
    from anthill.web import mcp_store

    class _Cfg:
        mcp_server_enabled = mcp_expose_wiki = mcp_expose_cache = mcp_expose_memory = True

    assert exposed_resources(_Cfg()) == ["wiki"]
    assert {t["name"] for t in tool_specs(_Cfg())} == {"query_wiki"}
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.commit()
    assert mcp_store._answer(s, org.id, "cache", Q_GONE) == "(this tool is not available)"
    assert mcp_store._answer(s, org.id, "memory", Q_GONE) == "(this tool is not available)"
    s.close()


@pytest.mark.usefixtures("embeds")
def test_the_shared_answer_index_is_not_asked_unless_an_operator_turns_it_on(tmp_path, monkeypatch):
    ws = _world(tmp_path / "w")
    asked: list[int] = []
    monkeypatch.setattr(
        ask_mod, "_central_lookup", lambda *a, **k: asked.append(1) or "an answer from the index"
    )
    monkeypatch.setattr(ask_mod, "_central_publish", lambda *a, **k: None)
    monkeypatch.delenv("ANTHILL_ORG_INDEX_ANSWERS", raising=False)
    ask_mod.ask(ws, Q_GONE, _Backend(), org_url="http://org.invalid")
    assert asked == []
    monkeypatch.setenv("ANTHILL_ORG_INDEX_ANSWERS", "1")
    assert (
        ask_mod.ask(ws, Q_GONE + " again", _Backend(), org_url="http://org.invalid")[0]
        == "an answer from the index"
    )
    assert asked == [1]


def test_removing_a_page_takes_it_out_of_the_search_index(tmp_path, monkeypatch):
    from anthill.search import meili

    ws = _world(tmp_path / "w")
    deleted: list[tuple] = []
    monkeypatch.setattr(meili, "enabled", lambda: True)
    monkeypatch.setattr(
        meili, "delete_pages", lambda slugs, index="": deleted.append((tuple(slugs), index)) or True
    )
    monkeypatch.setattr(meili, "index_pages", lambda *a, **k: True)
    removal.remove_page(ws, "ok-page")
    assert deleted == [(("ok-page",), ws.meili_index)]


# ── earlier turns of a chat ───────────────────────────────────────────────────


class _Msg:
    def __init__(self, id, role, content="", manifest=""):
        self.id, self.role, self.content, self.manifest = id, role, content, manifest


def _record_for(ws, slug):
    return answer_record.dumps(answer_record.build([ws.wiki / f"{slug}.md"], [], [ws]))


def test_an_earlier_answer_that_used_a_removed_item_is_not_replayed_with_its_question(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    ws = Workspace(tmp_path / "wikis" / "user-1", scope="personal")
    ws.init()
    ws.write_page("walrus-page", "# W\n\nThe cobalt walrus guards the lantern archive.\n")
    ws.write_page("quokka-page", "# Q\n\nThe quokka sanctuary opens at dawn.\n")
    turns = [
        _Msg(1, "user", "who guards it"), _Msg(2, "assistant", "the walrus", _record_for(ws, "walrus-page")),
        _Msg(3, "user", "when does it open"), _Msg(4, "assistant", "at dawn", _record_for(ws, "quokka-page")),
        _Msg(5, "user", "older question"), _Msg(6, "assistant", "an older answer with no record"),
    ]  # fmt: skip
    assert answer_record.stale_turns(turns) == set()  # nothing removed yet: everything is replayed
    removal.remove_page(ws, "walrus-page")
    assert answer_record.stale_turns(turns) == {
        1,
        2,
    }  # the answer and the question it answered; the rest stay


def test_a_record_that_cannot_be_checked_is_treated_as_no_longer_readable():
    item = {"kind": "page", "slug": "x", "space": "team", "space_ref": "not-a-team"}
    assert answer_record.still_eligible(item) is False


def test_the_connector_page_does_not_offer_the_cache_and_memory_tools(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    from tests.test_upload_status import _login, _setup

    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["admin"])
    res = client.get("/connectors/mcp")
    assert res.status_code == 200
    soup = BeautifulSoup(res.text, "html.parser")
    assert soup.find("input", attrs={"name": "expose_cache"}) is None
    assert soup.find("input", attrs={"name": "expose_memory"}) is None
    text = soup.get_text(" ", strip=True)
    assert (
        "Cache - not available in this version" in text
        and "Memory - not available in this version" in text
    )
