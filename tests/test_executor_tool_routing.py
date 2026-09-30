"""The agent loop drives tools through ``backend.chat_with_tools`` on ANY backend.

Previously a non-Ollama (OpenAI-compatible / org cloud) backend silently fell back to plain,
tool-less chat, and the Ollama path surfaced raw connect errors. Now both backends expose
``chat_with_tools`` returning an Ollama-shaped dict, so org chats are fully agentic and errors
are human-readable.
"""

import pytest

from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import Tool
from anthill.inference import openai_compat as oc
from anthill.inference.base import BackendError, Message


def _echo_tool(sink):
    return Tool(
        name="echo",
        description="echo text back",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        fn=lambda text: sink.append(text) or f"echoed {text}",
    )


class _FakeToolBackend:
    """Returns one tool call, then a final answer - in the normalized Ollama shape."""

    model = "m"

    def __init__(self):
        self.calls = 0
        self.tools_seen = None

    def chat_with_tools(self, messages, tools, *, temperature=0.1, model=None):
        self.calls += 1
        self.tools_seen = tools
        if self.calls == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "echo", "arguments": '{"text": "hi"}'}}],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}


def test_executor_drives_tools_on_a_non_ollama_backend():
    sink = []
    be = _FakeToolBackend()
    ex = AgentExecutor(be, [_echo_tool(sink)], max_steps=3)
    res = ex.run("say hi")
    assert sink == ["hi"]  # the tool actually ran
    assert res.answer == "done"
    assert be.tools_seen and be.tools_seen[0]["function"]["name"] == "echo"  # specs were passed


class _RaisingBackend:
    model = "m"

    def chat_with_tools(self, *a, **k):
        raise BackendError("Can't reach Ollama at http://localhost:11434. Run `ollama serve`.")


def test_executor_propagates_friendly_backend_error():
    ex = AgentExecutor(_RaisingBackend(), [], max_steps=2)
    with pytest.raises(BackendError) as exc:
        ex.run("hi")
    assert "Can't reach Ollama" in str(exc.value)
    assert "Errno" not in str(exc.value)


def test_streaming_executor_propagates_backend_error():
    ex = AgentExecutor(_RaisingBackend(), [], max_steps=2)
    with pytest.raises(BackendError) as exc:
        list(ex.stream("hi"))
    assert "Can't reach Ollama" in str(exc.value)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_openai_chat_with_tools_normalizes_to_ollama_shape(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
                }
            }
        ]
    }
    monkeypatch.setattr(oc.httpx, "post", lambda *a, **k: _FakeResp(payload))
    be = oc.OpenAICompatBackend("https://endpoint/v1", "m")
    out = be.chat_with_tools([Message("user", "hi")], [{"type": "function"}])
    assert out["message"]["content"] == "hello"
    assert out["message"]["tool_calls"][0]["function"]["name"] == "x"
