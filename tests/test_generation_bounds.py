"""Bound the free-form + research generation, and stream for real (chat-hang part 2, follow-up to #330).

#330 bounded only the structured json_chat calls; a runaway free-form answer or research synthesis still
blocked the (non-streamed) chat path, producing zero bytes until the client timed out. These pin the
generous caps on the free-form answer path and research synthesis, and the num_predict plumbing through
the streaming primitives. Finding: qa/chat-eval/DEV_FINDINGS.md."""

from anthill import research
from anthill.inference.ollama import OllamaBackend
from anthill.wiki import ask as ask_mod
from anthill.wiki.ask import ANSWER_MAX_TOKENS, stream_chat
from anthill.wiki.workspace import Workspace


class _Backend:
    def chat(self, messages, **k):
        return "a plain answer"


def test_ollama_stream_payload_carries_num_predict():
    be = OllamaBackend("http://x", "m")
    p = be._payload([], temperature=0.2, model=None, stream=True, num_predict=2048)
    assert p["options"]["num_predict"] == 2048 and p["stream"] is True


def test_ask_bounds_the_free_form_answer_generation(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    ws.init()
    seen = {}

    def _fake_chat(backend, messages, model_override, *, num_predict=None):
        seen["num_predict"] = num_predict
        return "ok"

    monkeypatch.setattr(ask_mod, "_chat", _fake_chat)
    ask_mod.ask(ws, "explain the deployment process", _Backend())
    assert seen["num_predict"] == ANSWER_MAX_TOKENS  # the answer generation is capped


def test_research_synthesis_is_bounded():
    seen = {}

    class _BE:
        def chat(self, messages, *, num_predict=None):
            seen["num_predict"] = num_predict
            return "report"

    class _Src:
        title, url, body, snippet = "T", "http://x", "b" * 100, "s"

    out = research._synthesize(_BE(), "topic", [_Src()])
    assert out == "report" and seen["num_predict"] == research.RESEARCH_MAX_TOKENS


def test_research_synthesis_degrades_if_backend_rejects_num_predict():
    class _BE:
        def chat(self, messages):  # no num_predict kwarg -> TypeError on the first attempt
            return "report"

    class _Src:
        title, url, body, snippet = "T", "http://x", "b", "s"

    assert research._synthesize(_BE(), "topic", [_Src()]) == "report"


def test_stream_chat_passes_num_predict_and_degrades():
    got = {}

    class _Streamer:
        def chat_stream(self, messages, *, model=None, num_predict=None):
            got["num_predict"] = num_predict
            yield "tok"

    assert list(stream_chat(_Streamer(), [], num_predict=1234)) == ["tok"]
    assert got["num_predict"] == 1234

    class _OldStreamer:
        def chat_stream(self, messages, *, model=None):  # no num_predict
            yield "tok"

    # must not raise - retries without the unsupported kwarg
    assert list(stream_chat(_OldStreamer(), [], num_predict=1234)) == ["tok"]


def test_agent_step_generation_is_bounded():
    # the tool-calling path (used by scheduled tasks, e.g. task_exec_math) must also cap generation,
    # or a reasoning model can run one step past the task's time budget
    from anthill.agent.executor import STEP_MAX_TOKENS, AgentExecutor

    seen = {}

    class _BE:
        model = "m"

        def chat_with_tools(self, messages, tools, *, model=None, num_predict=None):
            seen["num_predict"] = num_predict
            return {"message": {"content": "done", "tool_calls": []}}

    AgentExecutor(_BE(), [], max_steps=1).run("compute 47*89")
    assert seen["num_predict"] == STEP_MAX_TOKENS


def test_agent_step_degrades_if_backend_rejects_num_predict():
    from anthill.agent.executor import AgentExecutor

    class _BE:
        model = "m"

        def chat_with_tools(self, messages, tools, *, model=None):  # no num_predict kwarg
            return {"message": {"content": "done", "tool_calls": []}}

    # must not crash on the unsupported kwarg - it retries without it
    assert AgentExecutor(_BE(), [], max_steps=1).run("hi").answer == "done"
