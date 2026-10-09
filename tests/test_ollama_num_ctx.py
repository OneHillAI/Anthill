"""#109: Anthill asks Ollama for an explicit context window on every request. Model-free."""

import json

import pytest

import anthill.inference.ollama as om
from anthill.inference.base import Message

# The suite stubs chat_with_tools so no test reaches a live engine; keep the real one to test its payload.
_REAL_CHAT_WITH_TOOLS = om.OllamaBackend.chat_with_tools


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    om._CTX_WINDOW_CACHE.clear()
    monkeypatch.delenv("ANTHILL_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH", raising=False)
    monkeypatch.setattr(
        om, "_total_memory_gib", lambda: 16.0
    )  # a 16 GB Mac unless a test says otherwise


def _options(payload):
    return payload["options"]


def test_the_default_window_is_8192():
    assert om.DEFAULT_NUM_CTX == 8192
    assert om.OllamaBackend("http://x", "m").num_ctx == 8192


def test_the_env_variable_overrides_the_default(monkeypatch):
    monkeypatch.setenv("ANTHILL_NUM_CTX", "16384")
    assert om.OllamaBackend("http://x", "m").num_ctx == 16384


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "100", "2000", "4095", "12.5"])
def test_a_bad_env_value_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setenv("ANTHILL_NUM_CTX", bad)
    assert om.configured_num_ctx() == 8192


def test_the_constructor_argument_wins_over_the_env(monkeypatch):
    monkeypatch.setenv("ANTHILL_NUM_CTX", "16384")
    assert om.OllamaBackend("http://x", "m", num_ctx=4096).num_ctx == 4096


def test_every_chat_payload_carries_num_ctx():
    be = om.OllamaBackend("http://x", "m")
    msgs = [Message("user", "hi")]
    assert _options(be._payload(msgs, temperature=0.2, model=None, stream=False))["num_ctx"] == 8192
    assert _options(be._payload(msgs, temperature=0.2, model=None, stream=True))["num_ctx"] == 8192
    big = om.OllamaBackend("http://x", "m", num_ctx=16384)
    assert (
        _options(big._payload(msgs, temperature=0.2, model=None, stream=False))["num_ctx"] == 16384
    )


def test_the_tool_call_payload_carries_num_ctx(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "ok"}}

    def _post(url, json=None, **k):
        seen["payload"] = json
        return _Resp()

    monkeypatch.setattr(om.httpx, "post", _post)
    monkeypatch.setattr(om.OllamaBackend, "chat_with_tools", _REAL_CHAT_WITH_TOOLS)
    om.OllamaBackend("http://x", "m", num_ctx=12288).chat_with_tools([Message("user", "hi")], [])
    assert _options(seen["payload"])["num_ctx"] == 12288
    json.dumps(seen["payload"])  # still serialisable


def test_the_requested_window_is_capped_at_the_models_trained_maximum_once_known():
    be = om.OllamaBackend("http://x", "tiny", num_ctx=8192)
    msgs = [Message("user", "hi")]
    assert _options(be._payload(msgs, temperature=0.2, model=None, stream=False))["num_ctx"] == 8192
    om._CTX_WINDOW_CACHE[("http://x", "tiny")] = 2048  # what /api/show reported for this model
    assert _options(be._payload(msgs, temperature=0.2, model=None, stream=False))["num_ctx"] == 2048
    # a different model on the same engine is not affected
    assert (
        _options(be._payload(msgs, temperature=0.2, model="other", stream=False))["num_ctx"] == 8192
    )


def test_building_a_request_makes_no_network_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("a payload must not probe the engine")

    monkeypatch.setattr(om.httpx, "post", _boom)
    monkeypatch.setattr(om.httpx, "get", _boom)
    om.OllamaBackend("http://x", "m")._payload(
        [Message("user", "hi")], temperature=0.2, model=None, stream=False
    )


def test_ollamas_own_variable_is_honoured_when_ours_is_not_set(monkeypatch):
    monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", "65536")
    assert om.configured_num_ctx() == 65536
    monkeypatch.setenv("ANTHILL_NUM_CTX", "12288")
    assert om.configured_num_ctx() == 12288  # ours wins


def test_a_bad_value_of_ollamas_variable_is_ignored(monkeypatch):
    monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", "1000")
    assert om.configured_num_ctx() == 8192


@pytest.mark.parametrize(
    ("memory_gib", "expected"),
    [
        (8, 8192),
        (16, 8192),
        (24, 8192),
        (31.9, 8192),
        (32, 32768),
        (48, 32768),
        (63.9, 32768),
        (64, 262144),
        (128, 262144),
    ],
)
def test_on_a_mac_the_default_never_falls_below_ollamas_own_for_its_memory(
    monkeypatch, memory_gib, expected
):
    monkeypatch.setattr(om, "_total_memory_gib", lambda: memory_gib)
    assert om.configured_num_ctx() == expected


def test_memory_is_only_read_on_a_mac(monkeypatch):
    monkeypatch.undo()  # the autouse stub off
    monkeypatch.setattr(om.sys, "platform", "linux")
    assert om._total_memory_gib() == 0.0
    monkeypatch.setattr(om.sys, "platform", "darwin")
    assert om._total_memory_gib() > 0


def test_the_minimum_is_above_the_answer_reserve():
    from anthill.inference import fit

    assert om._MIN_NUM_CTX > fit.ANSWER_RESERVE_TOKENS + fit.REFERENCE_FLOOR_TOKENS
