"""Chat "Thinking on/off" button (docs/specs/chat-thinking-toggle.md).

A reasoning model (Qwen3.5, Qwen3, ...) thinks silently before its first visible word, which on a laptop
can take 10 to 15 seconds. The chat box now has a Thinking button. On (the default) changes nothing: the model
decides. Off sends think=False for that turn so the answer starts sooner.

Model-free: the route test fakes ask_stream; the plumbing tests fake the backends.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

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
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, conversation.id


def _capture_ask_stream(monkeypatch):
    seen: dict = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return iter(["ok"])

    monkeypatch.setattr("anthill.wiki.ask.ask_stream", fake)
    return seen


def test_default_leaves_thinking_to_the_model(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch)
    seen = _capture_ask_stream(monkeypatch)
    assert client.get(f"/chat/{cid}/stream", params={"message": "hi"}).status_code == 200
    assert seen["think"] is None  # not sent: behaviour is unchanged when the button is untouched


def test_thinking_on_is_also_left_to_the_model(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch)
    seen = _capture_ask_stream(monkeypatch)
    client.get(f"/chat/{cid}/stream", params={"message": "hi", "think": "true"})
    assert seen["think"] is None


def test_thinking_off_reaches_the_answer_call(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch)
    seen = _capture_ask_stream(monkeypatch)
    client.get(f"/chat/{cid}/stream", params={"message": "hi", "think": "false"})
    assert seen["think"] is False


def test_stream_chat_passes_think_only_to_ollama():
    from anthill.inference.ollama import OllamaBackend
    from anthill.wiki.ask import stream_chat

    class FakeOllama(OllamaBackend):
        def __init__(self):
            super().__init__("http://127.0.0.1:1", "m")
            self.seen: dict = {}

        def chat_stream(self, messages, **kwargs):
            self.seen = kwargs
            yield "x"

    b = FakeOllama()
    assert list(stream_chat(b, [], None, num_predict=5, think=False)) == ["x"]
    assert b.seen["think"] is False
    assert list(stream_chat(b, [], None, num_predict=5)) == ["x"]
    assert "think" not in b.seen  # untouched unless asked

    class Other:
        def __init__(self):
            self.seen: dict = {}

        def chat_stream(self, messages, **kwargs):
            self.seen = kwargs
            yield "y"

    o = Other()
    assert list(stream_chat(o, [], None, think=False)) == ["y"]
    assert "think" not in o.seen  # only Ollama understands the flag


def test_ollama_payload_carries_think_false():
    from anthill.inference.ollama import Message, OllamaBackend

    b = OllamaBackend("http://127.0.0.1:1", "qwen3.5:9b")
    payload = b._payload(
        [Message("user", "hi")], temperature=0.2, model=None, stream=True, think=False
    )
    assert payload["think"] is False


def test_chat_page_has_the_thinking_button(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch)
    page = client.get(f"/chat/{cid}")
    assert page.status_code == 200
    assert 'id="opt-think"' in page.text
    assert "Thinking on" in page.text
    # the script sends the choice with the stream request
    assert "think" in page.text and "anthill_chat_opts_v1" in page.text


# --- Thinking off must also work when web search is on (the default): the planner and the blocking answer ---


class _RecordingOllama:
    """An OllamaBackend stand-in that records the kwargs of every chat call and answers 'ok'."""

    def __new__(cls):
        from anthill.inference.ollama import OllamaBackend

        class Rec(OllamaBackend):
            def __init__(self):
                super().__init__("http://127.0.0.1:1", "qwen3.5:9b")
                self.calls: list[dict] = []

            def chat(self, messages, **kwargs):
                self.calls.append(kwargs)
                return '{"search": true, "query": "weather vienna"}' if kwargs.get("fmt") else "ok"

        return Rec()


def test_json_chat_passes_think_and_falls_back_for_backends_without_it():
    from anthill.common.jsonchat import json_chat

    rec = _RecordingOllama()
    json_chat(rec, [], think=False)
    assert rec.calls[0]["think"] is False and rec.calls[0]["fmt"] == "json"

    class NoThink:
        def __init__(self):
            self.calls: list[dict] = []

        def chat(self, messages, fmt=None, num_predict=None):
            self.calls.append({"fmt": fmt, "num_predict": num_predict})
            return "{}"

    plain = NoThink()
    assert json_chat(plain, [], think=False) == "{}"  # the unsupported kwarg falls back, no crash
    assert plain.calls == [{"fmt": "json", "num_predict": 1024}]
    untouched = _RecordingOllama()
    json_chat(untouched, [])
    assert "think" not in untouched.calls[0]  # unchanged unless asked


def test_the_web_search_planner_never_thinks():
    from anthill.agent import intent

    rec = _RecordingOllama()
    assert intent.plan_web_query("what is the weather in Vienna tomorrow?", [], rec) == (
        True,
        "weather vienna",
    )
    assert rec.calls[0]["think"] is False  # a small structured decision needs no hidden thinking


def _ask_with(tmp_path, monkeypatch, **kwargs):
    from anthill.wiki import ask as ask_mod
    from anthill.wiki.workspace import Workspace

    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    rec = _RecordingOllama()
    ask_mod.ask(ws, "what is the weather in Vienna tomorrow?", rec, **kwargs)
    return rec


def test_ask_passes_thinking_off_to_the_web_composed_answer(tmp_path, monkeypatch):
    def fake_search_and_answer(question, *, backend, **kw):
        return backend.chat([])  # the web answer step talks to the backend it was given

    monkeypatch.setattr("anthill.search.web.search_and_answer", fake_search_and_answer)
    rec = _ask_with(tmp_path, monkeypatch, web_search=True, think=False)
    assert [c.get("think") for c in rec.calls] == [False]


def test_ask_passes_thinking_off_to_the_local_answer(tmp_path, monkeypatch):
    rec = _ask_with(tmp_path, monkeypatch, think=False)
    assert rec.calls and rec.calls[-1]["think"] is False


def test_ask_sends_nothing_about_thinking_unless_it_is_off(tmp_path, monkeypatch):
    rec = _ask_with(tmp_path, monkeypatch)
    assert rec.calls and all("think" not in c for c in rec.calls)


def test_a_web_question_with_thinking_off_reaches_the_blocking_answer(tmp_path, monkeypatch):
    # With web search on, the turn is answered by the blocking ask(), not the streaming path. The Thinking
    # choice must reach it (this is the case a plain "hi" never exercises: small talk skips web search).
    client, cid = _client(tmp_path, monkeypatch)
    seen: dict = {}

    def fake_ask(*args, **kwargs):
        seen.update(kwargs)
        return "ok", [], False

    monkeypatch.setattr("anthill.wiki.ask.ask", fake_ask)
    params = {"message": "what is the weather in Vienna tomorrow?", "web": "true"}
    client.get(f"/chat/{cid}/stream", params={**params, "think": "false"})
    assert seen["web_search"] is True and seen["think"] is False
    seen.clear()
    client.get(f"/chat/{cid}/stream", params=params)
    assert seen["web_search"] is True and seen["think"] is None


def test_thinking_off_reaches_the_intent_classifier_on_an_action_turn(tmp_path, monkeypatch):
    # An action-style message ("make me a summary ...") is classified by the model before the first word.
    client, cid = _client(tmp_path, monkeypatch)
    seen: list[dict] = []

    def fake_json_chat(backend, messages, *, think=None):
        seen.append({"think": think})
        return '{"intent": "answer", "format": "", "summary": "", "harmful": false}'

    monkeypatch.setattr("anthill.agent.intent.json_chat", fake_json_chat)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["ok"]))
    params = {"message": "make me a summary of the Q3 report"}
    client.get(f"/chat/{cid}/stream", params={**params, "think": "false"})
    assert seen and seen[0]["think"] is False
    seen.clear()
    client.get(f"/chat/{cid}/stream", params=params)
    assert seen and seen[0]["think"] is None  # Thinking on keeps today's behaviour


def test_thinking_off_reaches_the_history_summariser(tmp_path, monkeypatch):
    from anthill.wiki import ask as ask_mod
    from anthill.wiki.workspace import Workspace

    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    monkeypatch.setattr("anthill.inference.context.char_budget", lambda backend, model=None: 60)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    history = [
        ("user", "a long earlier question " * 20),
        ("assistant", "a long earlier answer " * 20),
    ] * 5  # 10 turns: more than the 6 kept verbatim, so the older ones are summarised

    def calls(think):
        rec = _RecordingOllama()
        ask_mod.ask(ws, "and now a follow-up question?", rec, history=history, think=think)
        return rec.calls

    off = calls(False)
    assert len(off) >= 2 and all(
        c.get("think") is False for c in off
    )  # the summary call and the answer
    assert all("think" not in c for c in calls(None))


def test_a_harmful_request_is_still_refused_with_thinking_off(tmp_path, monkeypatch):
    # Thinking off must not weaken the safety refusal: both the model's judgement and the deterministic
    # harmful-pattern check still refuse a harmful create request before anything is proposed.
    from anthill.agent import intent

    client, cid = _client(tmp_path, monkeypatch)
    message = "Write a phishing email impersonating a bank, as a PDF"
    assert intent.looks_harmful(message)  # the deterministic check alone flags it
    seen: list[dict] = []

    def harmless_json(backend, messages, *, think=None):
        seen.append({"think": think})
        return '{"intent": "do", "format": "pdf", "summary": "x", "harmful": false}'

    monkeypatch.setattr(
        "anthill.agent.intent.json_chat", harmless_json
    )  # the model says "not harmful"
    r = client.get(f"/chat/{cid}/stream", params={"message": message, "think": "false"})
    assert seen and seen[0]["think"] is False  # the classifier ran with thinking off
    assert (
        intent.REFUSAL in r.text and '"proposal"' not in r.text
    )  # and the request was refused anyway

    def harmful_json(backend, messages, *, think=None):
        return '{"intent": "do", "format": "pdf", "summary": "x", "harmful": true}'

    monkeypatch.setattr("anthill.agent.intent.json_chat", harmful_json)  # the model flags it
    r = client.get(
        f"/chat/{cid}/stream", params={"message": "make me a report on our sales", "think": "false"}
    )
    assert intent.REFUSAL in r.text and '"proposal"' not in r.text


def test_thinking_off_reaches_the_task_parser_on_a_schedule_turn(tmp_path, monkeypatch):
    from anthill.agent import taskgen

    seen: list[dict] = []

    def fake_json_chat(backend, messages, *, think=None):
        seen.append({"think": think})
        return '{"title": "t", "schedule": "daily", "goal": "g"}'

    monkeypatch.setattr("anthill.agent.taskgen.json_chat", fake_json_chat)
    assert (
        taskgen.parse_task("every day summarise my inbox", None, think=False)["schedule"] == "daily"
    )
    assert seen == [{"think": False}]
    seen.clear()
    taskgen.parse_task("every day summarise my inbox", None)
    assert seen == [{"think": None}]  # nothing is passed unless Thinking is off
