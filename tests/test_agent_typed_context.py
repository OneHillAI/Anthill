"""An agent run's reading of the wiki is typed knowledge blocks that each name their item, and the run has a record made from them
(build plan step 2.1, slice b). What the tools return to the model is what it was; ids are not written into it."""

import json

import pytest

from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import make_tools
from anthill.wiki import answer_record, prompt_blocks
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import embeds  # noqa: F401

pytestmark = pytest.mark.usefixtures("embeds")

GARDEN = "# Gardening\n\nTomatoes need sun and a weekly watering. The Flurf trowel is kept in the shed.\n"
OVEN = "# Ovens\n\nThe deck oven is heated to 250 degrees before the first bake.\n"


def _wiki(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page("gardening", GARDEN)
    ws.write_page("ovens", OVEN)
    return ws


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_reading_and_searching_give_the_model_what_they_gave_it_before(tmp_path):
    ws = _wiki(tmp_path)
    tools = make_tools(workspace=str(ws.root))
    got = _tool(tools, "read_wiki").call({"slug": "gardening"})
    assert got == (ws.wiki / "gardening.md").read_text()  # the page, byte for byte
    found = json.loads(_tool(tools, "search_wiki").call({"query": "Flurf trowel shed", "k": 2}))
    assert {f["slug"] for f in found} <= {"gardening", "ovens"} and all(
        set(f) == {"slug", "preview"} for f in found
    )
    assert "(not found" not in got and "(page" not in got


def test_the_record_lists_each_page_once_with_its_id_and_never_its_text(tmp_path):
    ws = _wiki(tmp_path)
    tools = make_tools(workspace=str(ws.root))
    ex = AgentExecutor(None, tools, principles="Answer in plain words.", model="m1")
    ex._begin("Name: Sam. The deck oven.")
    _tool(tools, "search_wiki").call({"query": "Flurf trowel shed", "k": 2}, ex._given)
    _tool(tools, "read_wiki").call({"slug": "gardening"}, ex._given)
    rec = ex.record()
    slugs = [i["slug"] for i in rec["items"]]
    assert slugs.count("gardening") == 1 and set(slugs) <= {
        "gardening",
        "ovens",
    }  # found and read: one item
    assert all(i["id"] and i["version"] for i in rec["items"])
    assert rec["route"] == "agent" and rec["model"] == "m1"
    assert [b["type"] for b in rec["blocks"]] == ["rules", "context"]
    stored = answer_record.dumps(rec)
    for secret in ("plain words", "Name: Sam", "Flurf", "weekly watering"):
        assert secret not in stored
    assert (
        answer_record.loads(stored)["items"] == rec["items"]
    )  # it can be stored and shown by the panel


def test_the_model_cannot_hand_a_tool_its_own_list_and_a_call_without_one_gives_nothing_to_record(
    tmp_path,
):
    ws = _wiki(tmp_path)
    tools = make_tools(workspace=str(ws.root))
    given: list = []
    assert "weekly watering" in _tool(tools, "read_wiki").call(
        {"slug": "gardening", "given": "x"}, given
    )
    assert (
        len(given) == 1 and given[0].item["slug"] == "gardening"
    )  # the list is the caller's, not the model's
    assert "weekly watering" in _tool(tools, "read_wiki").call(
        {"slug": "gardening"}
    )  # a direct call still works
    assert "weekly watering" in _tool(tools, "read_wiki").call(
        {"slug": "gardening", "given": "x"}
    )  # and the model's own "given" does not break it


def test_a_page_that_cannot_name_its_item_is_not_given_to_the_model(tmp_path, caplog, monkeypatch):
    ws = _wiki(tmp_path)
    monkeypatch.setattr(answer_record, "_page_item", lambda *a, **k: {"kind": "page"})
    tools = make_tools(workspace=str(ws.root))
    given: list = []
    with caplog.at_level("WARNING"):
        assert "not found" in _tool(tools, "read_wiki").call({"slug": "gardening"}, given)
        found = json.loads(
            _tool(tools, "search_wiki").call({"query": "Flurf trowel", "k": 3}, given)
        )
    assert found == [] and given == [] and "no item id" in caplog.text
    ex = AgentExecutor(None, tools)
    ex._begin("")
    assert ex.record() == {}  # nothing was given, so nothing is listed


def test_a_trashed_page_is_not_read_and_not_listed(tmp_path):
    ws = _wiki(tmp_path)
    path = ws.write_page("old", "# Old\n\nThe Plink secret is trashed.\n")
    path.write_text(path.read_text().replace("lifecycle: active", "lifecycle: trashed"))
    tools = make_tools(workspace=str(ws.root))
    ex = AgentExecutor(None, tools)
    ex._begin("")
    assert "not found" in _tool(tools, "read_wiki").call({"slug": "old"}, ex._given)
    _tool(tools, "search_wiki").call({"query": "Plink secret", "k": 5}, ex._given)
    assert "old" not in [i["slug"] for i in ex.record().get("items", [])]


class _Backend:
    """Calls search_wiki, then read_wiki, then answers."""

    model = "fake"

    def __init__(self):
        self.calls = 0

    def chat_with_tools(self, messages, tools, *, temperature=0.1, model=None):
        self.calls += 1
        step = {
            1: ("search_wiki", {"query": "Flurf trowel shed"}),
            2: ("read_wiki", {"slug": "gardening"}),
        }.get(self.calls)
        if step is None:
            return {"message": {"content": "In the shed.", "tool_calls": []}}
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": step[0], "arguments": json.dumps(step[1])}}],
            }
        }


def test_a_run_has_a_record_of_what_it_read_and_a_second_run_starts_clean(tmp_path):
    ws = _wiki(tmp_path)
    tools = make_tools(workspace=str(ws.root))
    ex = AgentExecutor(_Backend(), tools, max_steps=4, principles="Be brief.")
    first = ex.run("Where is the Flurf trowel?", context="Name: Sam.")
    assert first.answer == "In the shed." and "gardening" in [
        i["slug"] for i in first.record["items"]
    ]
    assert [b["type"] for b in first.record["blocks"]] == ["rules", "context"]
    ex.backend = type(
        "Quiet",
        (),
        {
            "model": "q",
            "chat_with_tools": lambda *a, **k: {"message": {"content": "Hi", "tool_calls": []}},
        },
    )()
    second = ex.run("Say hi")
    assert (
        second.record.get("items", []) == []
    )  # what the first run read is not in the second run's record


def test_the_streamed_run_has_a_record_after_it_ends(tmp_path):
    ws = _wiki(tmp_path)
    ex = AgentExecutor(_Backend(), make_tools(workspace=str(ws.root)), max_steps=4)
    "".join(ex.stream("Where is the Flurf trowel?"))
    assert "gardening" in [i["slug"] for i in ex.record()["items"]]


def test_block_kinds_that_need_an_item_still_refuse_without_one():
    with pytest.raises(prompt_blocks.MissingItemId):
        prompt_blocks.Block("page", "x", {})


def test_agent_mode_in_chat_stores_the_record_with_the_answer_and_shows_the_panel(
    tmp_path, monkeypatch
):
    import anthill.agent.executor as executor_mod
    from anthill.web.db import ChatMessage
    from tests.test_agent_web_tools_toggle import _client

    client, app_mod, conv_id = _client(tmp_path, monkeypatch)
    rec = {
        "items": [
            {
                "kind": "page",
                "slug": "gardening",
                "title": "Gardening",
                "id": "i-1",
                "version": 1,
                "space": "personal",
            }
        ],
        "route": "agent",
    }

    class _Executor:
        def __init__(self, backend, tools, **kw):
            pass

        def stream(self, goal, *, context=""):
            yield "In the shed."

        def record(self):
            return rec

    monkeypatch.setattr(executor_mod, "AgentExecutor", _Executor)
    resp = client.get(
        f"/chat/{conv_id}/stream", params={"message": "where is the trowel", "agent_mode": "true"}
    )
    assert resp.status_code == 200 and "record_html" in resp.text and "Gardening" in resp.text
    db = app_mod._SessionFactory()
    msg = db.query(ChatMessage).filter_by(conversation_id=conv_id, role="assistant").one()
    assert answer_record.loads(msg.manifest)["items"][0]["slug"] == "gardening"


def test_agent_mode_in_chat_survives_an_executor_that_cannot_make_a_record(tmp_path, monkeypatch):
    import anthill.agent.executor as executor_mod
    from anthill.web.db import ChatMessage
    from tests.test_agent_web_tools_toggle import _client

    client, app_mod, conv_id = _client(tmp_path, monkeypatch)

    class _Executor:
        def __init__(self, backend, tools, **kw):
            pass

        def stream(self, goal, *, context=""):
            yield "ok"

        def record(self):
            raise RuntimeError("no record")

    monkeypatch.setattr(executor_mod, "AgentExecutor", _Executor)
    resp = client.get(
        f"/chat/{conv_id}/stream", params={"message": "plan my week", "agent_mode": "true"}
    )
    assert resp.status_code == 200
    msg = (
        app_mod._SessionFactory()
        .query(ChatMessage)
        .filter_by(conversation_id=conv_id, role="assistant")
        .one()
    )
    assert msg.content.strip().endswith("ok") and not msg.manifest
