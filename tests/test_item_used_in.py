"""The item page shows where an item was used (#363): the answers the viewer may open whose record lists it. The lookup is
anthill/web/used_in.py; this tests the screen. Model-free."""

from datetime import datetime, timezone

from anthill.web import item_page
from anthill.web.db import Agent, AgentRun, ChatMessage, Conversation, ScheduledTask, TaskRun
from anthill.wiki import answer_record, items, removal
from tests.test_remove_an_item import _personal, _soup
from tests.test_upload_status import _login, _setup


def _record(ws, slug, **override):
    item = answer_record._page_item(ws.wiki / f"{slug}.md", ws)
    return {"v": 1, "items": [{**item, **override}], "more": 0}


def _chat(app_mod, who, record, title="A chat"):
    uid, org_id, _role = who
    s = app_mod._SessionFactory()
    try:
        conv = Conversation(user_id=uid, org_id=org_id, plane="solo", title=title)
        s.add(conv)
        s.flush()
        s.add(ChatMessage(conversation_id=conv.id, role="user", content="q"))
        msg = ChatMessage(
            conversation_id=conv.id,
            role="assistant",
            content="a",
            manifest=answer_record.dumps(record),
        )
        s.add(msg)
        s.commit()
        return conv.id, msg.id
    finally:
        s.close()


def _task_run(app_mod, who, record, title="A task"):
    uid, org_id, _role = who
    s = app_mod._SessionFactory()
    try:
        task = ScheduledTask(
            org_id=org_id, created_by=uid, title=title, goal="g", schedule="once", status="done"
        )
        s.add(task)
        s.flush()
        s.add(
            TaskRun(
                org_id=org_id,
                task_id=task.id,
                status="ok",
                result="r",
                record=answer_record.dumps(record),
                finished_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
        return task.id
    finally:
        s.close()


def _agent_run(app_mod, who, record, name="An agent"):
    uid, org_id, _role = who
    s = app_mod._SessionFactory()
    try:
        agent = Agent(org_id=org_id, created_by=uid, name=name, mandate="m", schedule="manual")
        s.add(agent)
        s.flush()
        s.add(
            AgentRun(
                org_id=org_id,
                agent_id=agent.id,
                status="ok",
                result="r",
                record=answer_record.dumps(record),
                finished_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
        return agent.id
    finally:
        s.close()


def _story(client, slug):
    return _soup(client, f"/wiki/item?kind=page&name={slug}&scope=personal").find(id="item-story")


def _used(story):
    return story.select("ul.item-used li")


def test_a_page_says_which_chat_task_and_agent_it_was_given_to(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    record = _record(ws, "ferry")
    conv, msg = _chat(app_mod, ids["owner"], record, title="Ferry timetable")
    task = _task_run(app_mod, ids["owner"], record, title="Weekly ferry check")
    agent = _agent_run(app_mod, ids["owner"], record, name="Travel agent")
    _login(client, ids["owner"])
    rows = _used(_story(client, "ferry"))
    assert len(rows) == 3
    by_title = {li.a.get_text(): li for li in rows}
    assert by_title["Ferry timetable"].a["href"] == f"/chat/{conv}#bubble-{msg}"
    assert by_title["Weekly ferry check"].a["href"] == f"/tasks/{task}/result"
    assert by_title["Travel agent"].a["href"] == f"/agents/{agent}"
    for li in rows:
        text = li.get_text(" ", strip=True)
        assert "given version 1" in text and "as it was then" in text


def test_the_standing_of_the_item_is_said_in_plain_words(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _chat(app_mod, ids["owner"], _record(ws, "ferry", hash="0" * 64), title="changed")
    _chat(app_mod, ids["owner"], _record(ws, "ferry", hash=""), title="unknown")
    _chat(
        app_mod,
        ids["owner"],
        _record(ws, "ferry", space="team", space_ref="team-99"),
        title="elsewhere",
    )
    _login(client, ids["owner"])
    said = {li.a.get_text(): li.get_text(" ", strip=True) for li in _used(_story(client, "ferry"))}
    assert "has changed since" in said["changed"]
    assert "given version 1" in said["unknown"]
    assert "changed" not in said["unknown"] and "as it was then" not in said["unknown"]
    assert "its wiki is not on this machine" in said["elsewhere"]


def test_a_removed_item_is_said_to_be_no_longer_available_to_answers(tmp_path, monkeypatch):
    app_mod, _eng, _client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    rec = items.read_page_record((ws.wiki / "walrus.md").read_text(encoding="utf-8"))
    _chat(app_mod, ids["owner"], _record(ws, "walrus"), title="Walrus chat")
    removal.remove_page(ws, "walrus")
    s = app_mod._SessionFactory()
    try:
        used = item_page.used_rows(s, rec, user_id=ids["owner"][0], org_id=ids["owner"][1])
    finally:
        s.close()
    assert [r["standing"] for r in used["rows"]] == ["no longer available to answers"]


def test_a_page_no_answer_used_says_so(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _chat(app_mod, ids["owner"], _record(ws, "ferry"), title="only ferry")
    _login(client, ids["owner"])
    story = _story(client, "ferry-checked")
    assert not _used(story)
    assert "None of your answers has been given this yet." in story.get_text()


def test_a_page_with_no_record_says_its_use_is_not_tracked(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    (ws.wiki / "loose.md").write_text("# Loose\n\nNo record.\n", encoding="utf-8")
    _login(client, ids["owner"])
    assert (
        "has no record yet, so where it is used is not tracked"
        in _story(client, "loose").get_text()
    )


def test_another_persons_answer_is_never_listed(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _chat(app_mod, ids["mate"], _record(ws, "ferry"), title="Someone else's chat")
    _task_run(app_mod, ids["mate"], _record(ws, "ferry"), title="Someone else's task")
    _login(client, ids["owner"])
    story = _story(client, "ferry")
    assert "Someone else" not in story.get_text()
    assert "None of your answers has been given this yet." in story.get_text()


def test_a_long_list_is_cut_at_twenty_and_counted(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    record = _record(ws, "ferry")
    for i in range(23):
        _chat(app_mod, ids["owner"], record, title=f"Chat {i}")
    _login(client, ids["owner"])
    story = _story(client, "ferry")
    assert len(_used(story)) == 20 and "and 3 more" in story.get_text()


def test_the_section_says_used_in_never_caused(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _chat(app_mod, ids["owner"], _record(ws, "ferry"))
    _login(client, ids["owner"])
    text = _story(client, "ferry").get_text(" ", strip=True)
    assert "Used in" in text and "caused" not in text.lower() and "relied" not in text.lower()


def test_a_document_has_the_section_too(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    from tests.test_remove_an_item import A

    doc = answer_record._document_item(A, [1], ws)
    _chat(app_mod, ids["owner"], {"v": 1, "items": [doc], "more": 0}, title="Quokka chat")
    _login(client, ids["owner"])
    story = _soup(client, f"/wiki/item?kind=document&name={A}&scope=personal").find(id="item-story")
    assert [li.a.get_text() for li in _used(story)] == ["Quokka chat"]
