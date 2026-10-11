"""A model that cannot call tools still answers (docs/specs/tasks-without-tools.md).

Ollama refuses a request that carries tools when the model has none (400 "does not support tools"), which
made every Task and agent run on such a model (the small default, gemma3:4b) fail at once. The executor now
asks the model's capabilities first and, for a model without tools, answers with one plain chat and says so
at the top of the answer. Model-free.
"""

import logging
import types

import httpx
import pytest

from anthill.agent.executor import (
    NO_TOOLS_NOTE,
    NO_TOOLS_REVIEW_REASON,
    STEP_MAX_TOKENS,
    AgentExecutor,
    AgentResult,
    strip_no_tools_note,
)
from anthill.agent.tools import Tool
from anthill.inference import ollama as ollama_mod
from anthill.inference.base import BackendError, Message, ToolsUnsupported
from anthill.inference.ollama import OllamaBackend

NOT_A_TOOL_MODEL = '{"error":"registry.ollama.ai/library/gemma3:4b does not support tools"}'

# The suite's autouse guard (tests/conftest.py) replaces this method with an "unreachable" stub; a test that
# needs the real one puts back only this method, so every other guard stays in place.
_REAL_CHAT_WITH_TOOLS = OllamaBackend.chat_with_tools


@pytest.fixture(autouse=True)
def _fresh_caches():
    ollama_mod._CAPABILITIES_CACHE.clear()
    yield
    ollama_mod._CAPABILITIES_CACHE.clear()


def _response(status, json_body=None, text=""):
    request = httpx.Request("POST", "http://localhost:11434/api/x")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text, request=request)


# ── reading the capability ───────────────────────────────────────────────────────


def test_a_model_that_lists_tools_can_use_them(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion", "tools"]})
    )
    assert OllamaBackend("http://localhost:11434", "qwen3:8b").can_use_tools() is True


def test_a_model_that_does_not_list_tools_cannot(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion", "vision"]})
    )
    assert OllamaBackend("http://localhost:11434", "gemma3:4b").can_use_tools() is False


def test_the_capability_is_read_once_per_model(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return _response(200, {"capabilities": ["completion"]})

    monkeypatch.setattr(httpx, "post", post)
    backend = OllamaBackend("http://localhost:11434", "gemma3:4b")
    assert backend.can_use_tools() is False and backend.can_use_tools() is False
    assert len(calls) == 1


def test_an_unreadable_capability_keeps_the_old_behaviour_and_is_not_remembered(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", down)
    backend = OllamaBackend("http://localhost:11434", "gemma3:4b")
    assert backend.can_use_tools() is True
    assert ollama_mod._CAPABILITIES_CACHE == {}
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion"]})
    )
    assert backend.can_use_tools() is False  # the next ask reads it properly


def test_an_old_ollama_without_capabilities_is_treated_as_able(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response(200, {"details": {}}))
    assert OllamaBackend("http://localhost:11434", "x").can_use_tools() is True


def test_a_400_not_supporting_tools_is_a_named_error_and_is_remembered(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_tools", _REAL_CHAT_WITH_TOOLS)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response(400, text=NOT_A_TOOL_MODEL))
    backend = OllamaBackend("http://localhost:11434", "gemma3:4b")
    with pytest.raises(ToolsUnsupported) as caught:
        backend.chat_with_tools([Message("user", "hi")], [{"type": "function"}])
    assert "gemma3:4b" in str(caught.value) and "registry.ollama.ai" not in str(caught.value)
    assert isinstance(caught.value, BackendError)
    assert backend.can_use_tools() is False  # learned without another call


def test_any_other_http_error_is_still_a_backend_error(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_tools", _REAL_CHAT_WITH_TOOLS)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response(500, text="boom"))
    with pytest.raises(BackendError) as caught:
        OllamaBackend("http://localhost:11434", "m").chat_with_tools([Message("user", "hi")], [])
    assert not isinstance(caught.value, ToolsUnsupported)


def test_one_read_serves_thinking_and_tools(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return _response(200, {"capabilities": ["completion", "thinking"]})

    monkeypatch.setattr(httpx, "post", post)
    backend = OllamaBackend("http://localhost:11434", "m")
    assert backend.can_think() is True and backend.can_use_tools() is False
    assert len(calls) == 1


def test_a_read_goes_stale_and_is_read_again(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion", "tools"]})
    )
    backend = OllamaBackend("http://localhost:11434", "m")
    assert backend.can_use_tools() is True
    key = ("http://localhost:11434", "m")
    stamp, caps = ollama_mod._CAPABILITIES_CACHE[key]
    ollama_mod._CAPABILITIES_CACHE[key] = (stamp - ollama_mod._CAPABILITIES_TTL - 1, caps)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion"]})
    )
    assert backend.can_use_tools() is False  # the model was replaced under the same tag


def test_deleting_a_model_forgets_what_was_read(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response(200, {"capabilities": ["completion"]})
    )
    backend = OllamaBackend("http://localhost:11434", "m")
    assert backend.can_use_tools() is False
    monkeypatch.setattr(httpx, "request", lambda *a, **k: _response(200, {}))
    assert backend.delete_model("m") is True
    assert ollama_mod._CAPABILITIES_CACHE == {}


# ── the executor ─────────────────────────────────────────────────────────────────


class FakeBackend:
    def __init__(self, *, can_use_tools=None, unsupported_on_request=False):
        self._can = can_use_tools
        self.unsupported_on_request = unsupported_on_request
        self.tool_requests = 0
        self.plain_requests = []
        if can_use_tools is not None:
            self.can_use_tools = lambda model=None: self._can

    def chat(self, messages, **kwargs):
        self.plain_requests.append(messages)
        self.plain_kwargs = kwargs
        return "A plain answer."

    def chat_with_tools(self, messages, tools, **kwargs):
        self.tool_requests += 1
        if self.unsupported_on_request:
            raise ToolsUnsupported("m does not support tools.")
        return {"message": {"content": "An answer with tools.", "tool_calls": []}}


def _tool():
    return Tool(
        name="web_search",
        description="search",
        parameters={"type": "object", "properties": {}},
        fn=lambda **k: "result",
    )


def test_a_model_without_tools_answers_with_one_plain_chat_and_says_so():
    backend = FakeBackend(can_use_tools=False)
    result = AgentExecutor(backend, [_tool()]).run("Summarise the week", context="Notes: x")
    assert backend.tool_requests == 0 and len(backend.plain_requests) == 1
    assert result.answer == NO_TOOLS_NOTE + "A plain answer."
    assert "can't use tools" in result.answer
    system = backend.plain_requests[0][0].content
    assert "no tools" in system and "access to tools" not in system
    assert "Notes: x" in backend.plain_requests[0][1].content  # the context still reaches the model


def test_a_model_with_tools_runs_as_before():
    backend = FakeBackend(can_use_tools=True)
    result = AgentExecutor(backend, [_tool()]).run("Find it")
    assert backend.tool_requests == 1 and backend.plain_requests == []
    assert result.answer == "An answer with tools."


def test_a_backend_that_cannot_say_keeps_the_old_behaviour():
    backend = FakeBackend()  # no can_use_tools at all, like a cloud backend
    result = AgentExecutor(backend, [_tool()]).run("Find it")
    assert backend.tool_requests == 1 and result.answer == "An answer with tools."


def test_a_run_with_no_tools_to_offer_is_not_treated_as_a_no_tools_run():
    backend = FakeBackend(can_use_tools=False)
    result = AgentExecutor(backend, []).run("Hello")
    assert NO_TOOLS_NOTE not in result.answer


def test_a_refusal_found_only_by_asking_still_falls_back():
    backend = FakeBackend(can_use_tools=True, unsupported_on_request=True)
    result = AgentExecutor(backend, [_tool()]).run("Find it")
    assert backend.tool_requests == 1 and len(backend.plain_requests) == 1
    assert result.answer == NO_TOOLS_NOTE + "A plain answer."


def test_streaming_a_model_without_tools_gives_the_note_and_the_answer():
    backend = FakeBackend(can_use_tools=False)
    text = "".join(AgentExecutor(backend, [_tool()]).stream("Find it"))
    assert text == NO_TOOLS_NOTE + "A plain answer." and backend.tool_requests == 0


def test_streaming_falls_back_when_the_request_is_refused():
    backend = FakeBackend(can_use_tools=True, unsupported_on_request=True)
    text = "".join(AgentExecutor(backend, [_tool()]).stream("Find it"))
    assert text.endswith("A plain answer.") and NO_TOOLS_NOTE in text


def test_the_plain_answer_comes_from_the_same_model_and_is_bounded():
    backend = FakeBackend(can_use_tools=False)
    AgentExecutor(backend, [_tool()], model="gemma3:4b").run("Find it")
    assert backend.plain_kwargs == {"model": "gemma3:4b", "num_predict": STEP_MAX_TOKENS}


def test_a_backend_whose_chat_takes_no_model_still_answers():
    class Narrow:
        can_use_tools = staticmethod(lambda model=None: False)

        def chat(self, messages):
            return "narrow"

    result = AgentExecutor(Narrow(), [_tool()], model="m").run("x")
    assert result.answer == NO_TOOLS_NOTE + "narrow"


def test_the_run_says_it_answered_without_tools():
    assert (
        AgentExecutor(FakeBackend(can_use_tools=False), [_tool()]).run("x").answered_without_tools
    )
    assert (
        not AgentExecutor(FakeBackend(can_use_tools=True), [_tool()])
        .run("x")
        .answered_without_tools
    )
    refused = AgentExecutor(FakeBackend(can_use_tools=True, unsupported_on_request=True), [_tool()])
    assert refused.run("x").answered_without_tools


def test_streaming_without_tools_gives_the_note_first_then_the_answer_as_it_comes():
    class Streaming(FakeBackend):
        def chat(self, messages, **kwargs):
            raise AssertionError("the stream must not wait for a whole chat")

        def chat_stream(self, messages, **kwargs):
            self.stream_kwargs = kwargs
            yield "one "
            yield "two"

    backend = Streaming(can_use_tools=False)
    chunks = list(AgentExecutor(backend, [_tool()], model="gemma3:4b").stream("Find it"))
    assert chunks == [NO_TOOLS_NOTE, "one ", "two"]
    assert backend.stream_kwargs == {"model": "gemma3:4b", "num_predict": STEP_MAX_TOKENS}


def test_a_second_run_skips_the_tool_request_once_a_refusal_is_learned(monkeypatch):
    # The real Ollama backend: the capability is unreadable (so the first run asks), Ollama refuses, and the
    # refusal is remembered - the second run does not send the tools again.
    monkeypatch.setattr(OllamaBackend, "chat_with_tools", _REAL_CHAT_WITH_TOOLS)
    monkeypatch.setattr(OllamaBackend, "chat", lambda self, messages, **k: "A plain answer.")
    posts = []

    def post(url, **kwargs):
        posts.append(url)
        if url.endswith("/api/show"):
            raise httpx.ConnectError("not readable")
        return _response(400, text=NOT_A_TOOL_MODEL)

    monkeypatch.setattr(httpx, "post", post)
    executor = AgentExecutor(OllamaBackend("http://localhost:11434", "gemma3:4b"), [_tool()])
    assert executor.run("one").answer == NO_TOOLS_NOTE + "A plain answer."
    asked = [u for u in posts if u.endswith("/api/chat")]
    assert len(asked) == 1
    assert executor.run("two").answer == NO_TOOLS_NOTE + "A plain answer."
    assert [u for u in posts if u.endswith("/api/chat")] == asked  # no second tool request


def test_strip_no_tools_note():
    assert strip_no_tools_note(NO_TOOLS_NOTE + "body") == "body"
    assert strip_no_tools_note("body") == "body"


def test_a_real_failure_of_the_plain_chat_is_still_reported():
    class Down(FakeBackend):
        def chat(self, messages, **kwargs):
            raise BackendError("Can't reach Ollama at localhost:11434.")

    with pytest.raises(BackendError):
        AgentExecutor(Down(can_use_tools=False), [_tool()]).run("x")


# ── a scheduled task end to end ──────────────────────────────────────────────────


def _run_one_task(tmp_path, monkeypatch, *, reviewer=None, created_by=None):
    """Tick one due task on a model without tools. Returns (TaskRun, ScheduledTask, notifications)."""
    from test_task_run_history import _seed_due_task

    from anthill.inference import base as base_mod
    from anthill.web import scheduler
    from anthill.web.db import ScheduledTask, TaskRun

    eng, session_factory, tid = _seed_due_task(tmp_path, monkeypatch)
    if created_by:
        from fk_seed import seed_org_and_users

        s = session_factory()
        seed_org_and_users(s, user_ids=(created_by,))
        s.get(ScheduledTask, tid).created_by = created_by
        s.commit()
        s.close()
        monkeypatch.setattr("anthill.web.recall.recall_memory", lambda *a, **k: "")
        monkeypatch.setattr(scheduler, "_remember_task_outcome", lambda *a, **k: [])
    backend = FakeBackend(can_use_tools=False)
    monkeypatch.setattr(base_mod, "build_backend", lambda config: backend)
    if reviewer is not None:
        monkeypatch.setattr("anthill.council.engine.review_completed_answer", reviewer)
    sent = []
    monkeypatch.setattr("anthill.web.notify.notify", lambda db, **kw: sent.append(kw))
    scheduler._tick(eng)
    s = session_factory()
    run = s.query(TaskRun).filter(TaskRun.task_id == tid).one()
    task = s.get(ScheduledTask, tid)
    assert backend.tool_requests == 0
    return run, task, sent


def test_a_task_on_a_model_without_tools_finishes_with_the_note(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        run, task, _sent = _run_one_task(tmp_path, monkeypatch)
    assert run.status == "ok" and not run.error
    assert run.result.startswith(NO_TOOLS_NOTE)
    assert task.last_result.startswith(NO_TOOLS_NOTE) and "ERROR" not in task.last_result


def test_such_a_run_is_flagged_for_review_and_the_owner_is_told(tmp_path, monkeypatch):
    run, task, sent = _run_one_task(tmp_path, monkeypatch, created_by=1)
    assert run.status == "ok"  # it did answer ...
    assert run.verify_needs_review is True and run.verify_reason == NO_TOOLS_REVIEW_REASON
    assert task.verify_needs_review is True  # ... but it is not a clean success
    assert len(sent) == 1 and sent[0]["user_id"] == 1
    assert sent[0]["title"] == "A task result needs your review"
    assert NO_TOOLS_REVIEW_REASON in sent[0]["body"]


def test_the_council_reviews_the_answer_not_the_note_and_the_note_stays(tmp_path, monkeypatch):
    seen = []

    def reviewer(cfg, goal, answer, backend, decrypt):
        seen.append(answer)
        return types.SimpleNamespace(answer="REVISED: " + answer)

    run, _task, _sent = _run_one_task(tmp_path, monkeypatch, reviewer=reviewer)
    assert seen == ["A plain answer."]  # the reviewer never saw the note
    assert run.result == NO_TOOLS_NOTE + "REVISED: A plain answer."


def test_a_task_memory_is_the_answer_not_the_note(tmp_path, monkeypatch):
    from test_agents_surface import _session

    from anthill.web import scheduler
    from anthill.web.db import MemoryItem, ScheduledTask

    db = _session(tmp_path, monkeypatch)
    task = ScheduledTask(org_id=1, created_by=1, title="Plan", goal="g", schedule="once")
    db.add(task)
    db.commit()
    monkeypatch.setattr("anthill.memory.embed_text", lambda text: [0.1, 0.2])
    monkeypatch.setattr("anthill.memory.is_semantically_new", lambda *a, **k: True)
    monkeypatch.setattr("anthill.web.memory_ops.auto_memory_on", lambda db, uid: True)
    monkeypatch.setattr("anthill.web.memory_ops.maybe_corroborate", lambda *a, **k: None)
    scheduler._remember_task_outcome(db, task, NO_TOOLS_NOTE + "Next week has three meetings.")
    db.commit()
    texts = [m.text for m in db.query(MemoryItem).all()]
    assert texts == ["Task 'Plan': Next week has three meetings."]


# ── an agent run ─────────────────────────────────────────────────────────────────


def _agent(db, **kw):
    from anthill.web.db import Agent

    agent = Agent(
        org_id=1, created_by=1, name="Scout", mandate="write the digest page", plane="solo", **kw
    )
    db.add(agent)
    db.commit()
    return agent


def test_an_agent_run_without_tools_keeps_the_note_and_hides_it_from_the_review(
    tmp_path, monkeypatch
):
    from test_agents_surface import _session

    from anthill.web import agents_run

    db = _session(tmp_path, monkeypatch)

    class _Executor:
        def __init__(self, backend, tools, **kw):
            pass

        def run(self, goal, *, context=""):
            return AgentResult(
                answer=NO_TOOLS_NOTE + "A plain answer.", answered_without_tools=True
            )

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _Executor)
    seen = []

    def reviewer(cfg, goal, answer, backend, decrypt):
        seen.append(answer)
        return types.SimpleNamespace(answer="REVISED: " + answer)

    monkeypatch.setattr("anthill.council.engine.review_completed_answer", reviewer)
    agent = _agent(db, governance="standard")
    answer = agents_run.run_agent(agent, db)
    assert seen == ["A plain answer."]
    assert answer == NO_TOOLS_NOTE + "REVISED: A plain answer."
    assert agent._answered_without_tools is True


def test_an_agent_tick_flags_a_run_that_answered_without_tools(tmp_path, monkeypatch):
    from test_agents_surface import _seed_due_agent

    from anthill.web import scheduler
    from anthill.web.db import Agent, AgentRun

    eng, Session, aid = _seed_due_agent(tmp_path, monkeypatch)

    def run_agent(agent, db):
        agent._answered_without_tools = True
        return NO_TOOLS_NOTE + "A plain answer."

    monkeypatch.setattr("anthill.web.agents_run.run_agent", run_agent)
    sent = []
    monkeypatch.setattr("anthill.web.notify.notify", lambda db, **kw: sent.append(kw))
    scheduler._agent_tick(eng)
    s = Session()
    run = s.query(AgentRun).filter(AgentRun.agent_id == aid).one()
    assert run.status == "ok" and run.verify_needs_review is True
    assert run.verify_reason == NO_TOOLS_REVIEW_REASON
    assert s.get(Agent, aid).verify_needs_review is True
    assert len(sent) == 1 and "needs your review" in sent[0]["title"]


def test_an_agent_memory_is_the_answer_not_the_note(tmp_path, monkeypatch):
    from test_agents_surface import _session

    from anthill.web import scheduler
    from anthill.web.db import MemoryItem

    db = _session(tmp_path, monkeypatch)
    agent = _agent(db, governance="standard")
    monkeypatch.setattr("anthill.memory.embed_text", lambda text: [0.1, 0.2])
    monkeypatch.setattr("anthill.memory.is_semantically_new", lambda *a, **k: True)
    monkeypatch.setattr("anthill.web.memory_ops.auto_memory_on", lambda db, uid: True)
    monkeypatch.setattr("anthill.web.memory_ops.maybe_corroborate", lambda *a, **k: None)
    scheduler._remember_agent_outcome(agent, NO_TOOLS_NOTE + "Competitor shipped X.", db)
    db.commit()
    assert [m.text for m in db.query(MemoryItem).all()] == ["Agent 'Scout': Competitor shipped X."]


# ── the chat ─────────────────────────────────────────────────────────────────────


def _chat(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    session.add(user)
    session.flush()
    session.add(OrgSettings(org_id=org.id, deployment_topology="solo"))
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id, org.id


class _SpyExecutor:
    runs = 0

    def __init__(self, backend, tools, **kw):
        pass

    def stream(self, goal, *, context=""):
        type(self).runs += 1
        yield "Agent answer"


DEEP_QUESTION = (
    "What changed in the plan? Why did it change?"  # two questions: the router calls it deep
)


def test_chat_agent_mode_still_runs_the_agent_on_a_model_without_tools(tmp_path, monkeypatch):
    client, _app, cid, _org = _chat(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: False)
    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _SpyExecutor)
    _SpyExecutor.runs = 0
    r = client.get(f"/chat/{cid}/stream", params={"message": "plan my week", "agent_mode": "true"})
    assert (
        r.status_code == 200 and _SpyExecutor.runs == 1
    )  # explicit agent mode keeps its note path


def test_chat_does_not_detour_into_the_agent_on_a_model_without_tools(tmp_path, monkeypatch):
    client, _app, cid, _org = _chat(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: False)
    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _SpyExecutor)
    _SpyExecutor.runs = 0
    r = client.get(f"/chat/{cid}/stream", params={"message": DEEP_QUESTION})
    assert r.status_code == 200
    assert _SpyExecutor.runs == 0  # the normal answer path (wiki, history) was used instead
    assert "Looking into this more thoroughly" not in r.text


def test_chat_still_goes_deeper_on_a_model_with_tools(tmp_path, monkeypatch):
    client, _app, cid, _org = _chat(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: True)
    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _SpyExecutor)
    _SpyExecutor.runs = 0
    r = client.get(f"/chat/{cid}/stream", params={"message": DEEP_QUESTION})
    assert r.status_code == 200 and _SpyExecutor.runs == 1
    assert "Looking into this more thoroughly" in r.text


# ── the Tasks form says so before anyone writes a task ───────────────────────────


def _tasks_page(tmp_path, monkeypatch):
    from test_task_run_history import _app, _auth

    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["b"], ids["org"], "member")
    return client


def test_the_new_task_form_warns_when_the_local_model_cannot_use_tools(tmp_path, monkeypatch):
    client = _tasks_page(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: False)
    page = client.get("/tasks").text
    assert 'id="no-tools-note"' in page and "can't use tools" in page.replace("&#39;", "'")
    assert "answers from your description" in page


def test_the_new_task_form_says_nothing_when_the_model_can_use_tools(tmp_path, monkeypatch):
    client = _tasks_page(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: True)
    assert 'id="no-tools-note"' not in client.get("/tasks").text


def test_the_tasks_page_still_loads_when_the_capability_cannot_be_read(tmp_path, monkeypatch):
    client = _tasks_page(tmp_path, monkeypatch)

    def broken(self, model=None, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(OllamaBackend, "can_use_tools", broken)
    r = client.get("/tasks")
    assert r.status_code == 200 and 'id="no-tools-note"' not in r.text


def test_the_note_is_for_a_new_task_not_for_editing_and_the_goal_points_to_it(
    tmp_path, monkeypatch
):
    client = _tasks_page(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: False)
    page = client.get("/tasks").text
    assert 'aria-describedby="no-tools-note"' in page
    assert "showNoToolsNote(true)" in page and "showNoToolsNote(false)" in page
    # the example in the box is a task a model without tools can do
    assert 'placeholder="e.g. Read my unread emails' not in page
    assert 'placeholder="e.g. Every weekday morning at 8, summarise my unread email' not in page


def test_with_tools_the_goal_examples_stay_as_they_were(tmp_path, monkeypatch):
    client = _tasks_page(tmp_path, monkeypatch)
    monkeypatch.setattr(OllamaBackend, "can_use_tools", lambda self, model=None, **kw: True)
    page = client.get("/tasks").text
    assert 'placeholder="e.g. Read my unread emails' in page
    assert 'aria-describedby="no-tools-note"' not in page


def test_no_note_is_looked_up_for_a_backend_that_is_not_ollama(tmp_path, monkeypatch):
    from test_agents_surface import _session

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings

    db = _session(tmp_path, monkeypatch)
    db.add(
        OrgSettings(
            org_id=1,
            deployment_topology="solo",
            local_serve_url="http://127.0.0.1:11435/v1",
            local_serve_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
        )
    )
    db.commit()
    asked = []
    monkeypatch.setattr(
        OllamaBackend, "can_use_tools", lambda self, model=None, **kw: asked.append(1) or False
    )
    org = db.get(Organization, 1)
    assert app_mod._local_model_without_tools(db, org) == ""
    assert asked == []
