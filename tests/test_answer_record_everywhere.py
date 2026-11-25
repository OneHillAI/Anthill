"""The record of what an answer was given is kept on every route that keeps an answer, and the answers an item was used in can be
found (build plan step 2.2, docs/specs/answer-record-everywhere.md). Model-free."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web import used_in as used_in_mod
from anthill.web.db import (
    Agent,
    AgentRun,
    ChatMessage,
    Conversation,
    ScheduledTask,
    TaskRun,
)
from anthill.wiki import answer_record
from anthill.wiki.workspace import workspace_for


@pytest.fixture
def world(tmp_path, monkeypatch):
    from fk_seed import seed_org_and_users

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False)()
    seed_org_and_users(s, user_ids=(1, 2))
    s.commit()
    ws = workspace_for("personal", user_id=1)
    ws.init()
    path = ws.write_page("gardening", "# Gardening\n\nTomatoes need sun.\n")
    other = ws.write_page("ovens", "# Ovens\n\nThe deck oven.\n")
    return s, ws, path, other


def _record(*paths, ws):
    return {
        "v": 1,
        "items": [answer_record._page_item(p, ws) for p in paths],
        "more": 0,
    }


def _chat(db, user_id, record, title="A chat", org_id=1):
    conv = Conversation(org_id=org_id, user_id=user_id, title=title)
    db.add(conv)
    db.flush()
    db.add(ChatMessage(conversation_id=conv.id, role="user", content="q"))
    msg = ChatMessage(
        conversation_id=conv.id, role="assistant", content="a", manifest=answer_record.dumps(record)
    )
    db.add(msg)
    db.commit()
    return conv.id


def _task_run(db, user_id, record, title="A task"):
    task = ScheduledTask(
        org_id=1, created_by=user_id, title=title, goal="g", schedule="once", status="done"
    )
    db.add(task)
    db.flush()
    db.add(
        TaskRun(
            org_id=1, task_id=task.id, status="ok", result="r", record=answer_record.dumps(record)
        )
    )
    db.commit()


def _agent_run(db, user_id, record, name="An agent"):
    agent = Agent(org_id=1, created_by=user_id, name=name, mandate="m", schedule="manual")
    db.add(agent)
    db.flush()
    db.add(
        AgentRun(
            org_id=1, agent_id=agent.id, status="ok", result="r", record=answer_record.dumps(record)
        )
    )
    db.commit()


# ---- has the item changed since ----------------------------------------------------------------------------------------


def test_changed_since_says_unchanged_changed_gone_and_not_held(world):
    _db, ws, path, other = world
    rec = _record(path, other, ws=ws)
    assert {i["title"]: i["status"] for i in answer_record.changed_since(rec)} == {
        "Gardening": "unchanged",
        "Ovens": "unchanged",
    }
    ws.write_page(
        "gardening", "# Gardening\n\nTomatoes need sun and water.\n"
    )  # edited after the answer
    other.unlink()  # removed
    got = {i["title"]: i["status"] for i in answer_record.changed_since(rec)}
    assert got == {"Gardening": "changed", "Ovens": "gone"}
    elsewhere = {"items": [{**rec["items"][0], "space": "team", "space_ref": "team-99"}]}
    assert answer_record.changed_since(elsewhere)[0]["status"] == "not_held"
    assert (
        answer_record.changed_since({"items": [{**rec["items"][0], "hash": ""}]})[0]["status"]
        == "unknown"
    )


# ---- used in -----------------------------------------------------------------------------------------------------------


def test_used_in_finds_the_chat_the_task_and_the_agent_that_were_given_a_page_and_no_one_elses(
    world,
):
    db, ws, path, other = world
    item_id = answer_record._page_item(path, ws)["id"]
    _chat(db, 1, _record(path, ws=ws), title="Mine")
    _task_run(db, 1, _record(path, other, ws=ws), title="Mine too")
    _agent_run(db, 1, _record(path, ws=ws), name="My agent")
    _chat(db, 1, _record(other, ws=ws), title="Did not use it")
    _chat(db, 2, _record(path, ws=ws), title="Someone else's chat")
    _task_run(db, 2, _record(path, ws=ws), title="Someone else's task")
    _agent_run(db, 2, _record(path, ws=ws), name="Someone else's agent")
    rows = used_in_mod.used_in(db, item_id, user_id=1, org_id=1)
    assert sorted((r["route"], r["title"]) for r in rows) == [
        ("agent", "My agent"),
        ("chat", "Mine"),
        ("task", "Mine too"),
    ]
    assert all(r["status"] == "unchanged" and r["version"] == 1 for r in rows)
    assert (
        used_in_mod.used_in(db, item_id, user_id=2, org_id=1)
        and len(used_in_mod.used_in(db, item_id, user_id=2, org_id=1)) == 3
    )
    assert (
        used_in_mod.used_in(db, "", user_id=1, org_id=1) == []
        and used_in_mod.used_in(db, "no-such-id", user_id=1, org_id=1) == []
    )


def test_used_in_says_when_the_page_has_changed_since_and_lists_an_answer_once(world):
    db, ws, path, _other = world
    item = answer_record._page_item(path, ws)
    twice = {"v": 1, "items": [item, item], "more": 0}  # a page given twice to one answer
    _chat(db, 1, twice)
    ws.write_page("gardening", "# Gardening\n\nTomatoes need sun and water.\n")
    rows = used_in_mod.used_in(db, item["id"], user_id=1, org_id=1)
    assert len(rows) == 1 and rows[0]["status"] == "changed"


def test_used_in_lists_the_newest_first_and_stays_within_the_viewers_org(world):
    db, ws, path, _other = world
    item_id = answer_record._page_item(path, ws)["id"]
    _chat(db, 1, _record(path, ws=ws), title="Older")
    _chat(db, 1, _record(path, ws=ws), title="Newer")
    msgs = (
        db.query(ChatMessage).filter(ChatMessage.role == "assistant").order_by(ChatMessage.id).all()
    )
    msgs[0].created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    assert [r["title"] for r in used_in_mod.used_in(db, item_id, user_id=1, org_id=1)] == [
        "Newer",
        "Older",
    ]
    assert used_in_mod.used_in(db, item_id, user_id=1, org_id=2) == []


# ---- task and agent runs keep their record -----------------------------------------------------------------------------


def _tick_task(tmp_path, monkeypatch, run_task):
    from test_task_run_history import _seed_due_task

    from anthill.web import scheduler

    tmp_path.mkdir(parents=True, exist_ok=True)
    eng, S, tid = _seed_due_task(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, "_run_task", run_task)
    scheduler._tick(eng)
    return S().query(TaskRun).filter(TaskRun.task_id == tid).one()


def test_a_task_run_keeps_the_record_of_what_it_was_given(tmp_path, monkeypatch):
    rec = {
        "v": 1,
        "items": [{"kind": "page", "slug": "gardening", "id": "i-1", "version": 2}],
        "more": 0,
        "route": "agent",
    }

    def run_task(task, db):
        task._record = rec
        return "did the task"

    run = _tick_task(tmp_path, monkeypatch, run_task)
    assert answer_record.loads(run.record)["items"][0]["id"] == "i-1"


def test_a_failed_task_run_and_a_run_given_nothing_keep_no_record(tmp_path, monkeypatch):
    def boom(task, db):
        task._record = {"v": 1, "items": [{"id": "i-1"}], "more": 0}
        raise RuntimeError("kaboom")

    assert _tick_task(tmp_path / "a", monkeypatch, boom).record == ""
    assert _tick_task(tmp_path / "b", monkeypatch, lambda t, db: "ok").record == ""


def test_an_agent_run_keeps_the_record_and_a_failed_one_does_not(tmp_path, monkeypatch):
    from test_agents_surface import _seed_due_agent

    from anthill.web import scheduler

    rec = {
        "v": 1,
        "items": [{"kind": "page", "slug": "gardening", "id": "i-1", "version": 1}],
        "more": 0,
    }
    eng, Session, aid = _seed_due_agent(tmp_path, monkeypatch)

    def run_agent(agent, db):
        agent._record = rec
        return "done"

    monkeypatch.setattr("anthill.web.agents_run.run_agent", run_agent)
    monkeypatch.setattr("anthill.web.notify.notify", lambda db, **kw: None)
    scheduler._agent_tick(eng)
    run = Session().query(AgentRun).filter(AgentRun.agent_id == aid).one()
    assert run.status == "ok" and answer_record.loads(run.record)["items"][0]["id"] == "i-1"

    (tmp_path / "x").mkdir()
    eng2, Session2, aid2 = _seed_due_agent(tmp_path / "x", monkeypatch)

    def failing(agent, db):
        agent._record = rec
        raise RuntimeError("down")

    monkeypatch.setattr("anthill.web.agents_run.run_agent", failing)
    scheduler._agent_tick(eng2)
    assert Session2().query(AgentRun).filter(AgentRun.agent_id == aid2).one().record == ""


def test_an_escalated_task_answer_lists_only_the_context_it_was_sent_not_the_pages_the_run_read():
    from anthill.web import scheduler

    read = {"v": 1, "items": [{"kind": "page", "id": "i-1"}], "more": 0}
    assert scheduler._run_record("A local answer.", read, "ctx") == read
    escalated = scheduler._run_record(
        scheduler.ESCALATED_PREFIX + "From the provider.", read, "Name: Sam."
    )
    assert escalated["items"] == [] and escalated["route"] == "task-escalated"
    assert [b["type"] for b in escalated["blocks"]] == [
        "context"
    ] and "Sam" not in answer_record.dumps(escalated)


def test_a_run_is_stored_with_nothing_when_it_was_given_nothing():
    from anthill.web import scheduler

    assert scheduler._record_text({}) == "" and scheduler._record_text(None) == ""
    assert (
        answer_record.loads(scheduler._record_text({"v": 1, "items": [{"id": "x"}], "more": 0}))[
            "items"
        ][0]["id"]
        == "x"
    )


# ---- Slack keeps its record ---------------------------------------------------------------------------------------------


def test_a_slack_answer_is_stored_with_the_record_of_what_it_was_given(tmp_path, monkeypatch):
    from test_slack_bot import _app, _FakePlane, _post_event
    from test_slack_bot import sb as slack

    _, client, _ids = _app(tmp_path, monkeypatch)
    rec = {
        "v": 1,
        "items": [
            {"kind": "page", "slug": "deploys", "title": "Deploys", "id": "i-9", "version": 3}
        ],
        "more": 0,
    }

    def fake_ask(ws, q, backend, **k):
        k["on_record"](rec)
        return "Deploys are on Tuesdays.", [], False

    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", lambda *a, **k: _FakePlane())
    monkeypatch.setattr("anthill.web.agent_context.agent_context_for", lambda *a, **k: ("", []))
    monkeypatch.setattr("anthill.wiki.ask.ask", fake_ask)
    monkeypatch.setattr(slack, "user_email", lambda tok, uid: "ada@acme.com")
    monkeypatch.setattr(slack, "post_message", lambda *a, **k: None)
    r = _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT> when do we deploy?",
                "channel": "C1",
                "ts": "111.1",
            }
        },
    )
    assert r.status_code == 200
    s = app_mod._SessionFactory()
    msg = s.query(ChatMessage).filter(ChatMessage.role == "assistant").one()
    assert json.loads(msg.manifest)["items"][0]["id"] == "i-9"


def test_an_install_from_before_the_record_column_gets_it_and_keeps_its_runs(tmp_path):
    from sqlalchemy import inspect, text

    from anthill.web import migrate

    eng = create_engine(
        f"sqlite:///{tmp_path / 'old.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    with eng.begin() as conn:
        for table in ("task_runs", "agent_runs"):
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN record"))
        conn.execute(text("INSERT INTO organizations (id, name, slug) VALUES (1, 'A', 'a')"))
    assert "record" not in {c["name"] for c in inspect(eng).get_columns("task_runs")}
    added = migrate.ensure_columns(eng)
    assert {"task_runs.record", "agent_runs.record"} <= set(added)
    for table in ("task_runs", "agent_runs"):
        assert "record" in {c["name"] for c in inspect(eng).get_columns(table)}
