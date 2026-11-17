"""Per-turn model timings and cached-token counts (#219). Model-free: the payloads below were recorded from a
live Ollama 0.40.7 (qwen2.5:3b, 2026-11-17) and trimmed to the counters; nothing here calls a model."""

import contextlib
import dataclasses
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.inference.base import BackendError, Message
from anthill.inference.ollama import OllamaBackend
from anthill.inference.openai_compat import OpenAICompatBackend
from anthill.inference.stats import INT_FIELDS, TurnStats, from_ollama, from_openai
from anthill.web import metrics
from anthill.web.db import QueryMetric, create_tables

# Cold call: the model had to load, nothing was cached.
OLLAMA_COLD = {
    "model": "qwen2.5:3b",
    "message": {"role": "assistant", "content": "Hello there"},
    "done": True,
    "done_reason": "stop",
    "total_duration": 2006763959,
    "load_duration": 1819154625,
    "prompt_eval_count": 35,
    "prompt_eval_cached_count": 0,
    "prompt_eval_duration": 129410000,
    "eval_count": 3,
    "eval_duration": 53762000,
}
# The same prompt again: 34 of 35 tokens came from the cache.
OLLAMA_WARM = {
    "model": "qwen2.5:3b",
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "total_duration": 73200833,
    "load_duration": 481583,
    "prompt_eval_count": 35,
    "prompt_eval_cached_count": 34,
    "prompt_eval_duration": 23469000,
    "eval_count": 3,
    "eval_duration": 48189000,
}
# Ollama's /v1 usage chunk (stream_options.include_usage), with its timings.
OPENAI_USAGE_CHUNK = {
    "id": "chatcmpl-951",
    "choices": [],
    "usage": {
        "prompt_tokens": 35,
        "prompt_tokens_details": {"cached_tokens": 34},
        "completion_tokens": 3,
        "total_tokens": 38,
    },
    "timings": {"prompt_ms": 23, "predicted_ms": 46},
}

MSGS = [Message("user", "hi")]

# conftest stubs these for every test (no live model); keep the real ones to restore in the Ollama tests.
_REAL_CHAT = OllamaBackend.chat
_REAL_CHAT_WITH_TOOLS = OllamaBackend.chat_with_tools


@pytest.fixture(autouse=True)
def _real_ollama_methods(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat", _REAL_CHAT)
    monkeypatch.setattr(OllamaBackend, "chat_with_tools", _REAL_CHAT_WITH_TOOLS)


# -- parsing: absent means None, never 0 -------------------------------------------------------------


def test_ollama_cold_call_parses():
    s = from_ollama(OLLAMA_COLD, model="qwen2.5:3b", num_ctx=4096)
    assert s.prompt_tokens == 35 and s.cached_tokens == 0 and s.prefill_tokens == 35
    assert s.prefill_ms == 129 and s.decode_tokens == 3 and s.decode_ms == 54
    assert s.load_ms == 1819 and s.total_ms == 2007 and s.num_ctx == 4096
    assert s.first_token_ms is None  # only a streamed call is timed by us


def test_ollama_warm_call_shows_the_cached_prefix():
    s = from_ollama(OLLAMA_WARM, model="qwen2.5:3b", num_ctx=4096)
    assert s.cached_tokens == 34 and s.prefill_tokens == 1


def test_ollama_without_counters_gives_none_not_zero():
    s = from_ollama({"message": {"content": "x"}}, model="m", num_ctx=None, wall_ms=900)
    values = {f: getattr(s, f) for f in INT_FIELDS}
    assert {k: v for k, v in values.items() if v is not None} == {"total_ms": 900}
    assert s.prefill_tokens is None and s.decode_tokens_per_s is None and s.speed_line() is None


def test_an_engine_that_does_not_report_cached_tokens_is_not_a_zero():
    old = {k: v for k, v in OLLAMA_COLD.items() if k != "prompt_eval_cached_count"}
    s = from_ollama(old, model="m", num_ctx=4096)
    assert s.cached_tokens is None and s.prompt_tokens == 35


@pytest.mark.parametrize("bad", [None, "12", -5, True, [], {}])
def test_odd_values_are_not_measurements(bad):
    s = from_ollama({"eval_count": bad, "eval_duration": bad}, model="m", num_ctx=None)
    assert s.decode_tokens is None and s.decode_ms is None


def test_openai_usage_and_timings_parse():
    s = from_openai(OPENAI_USAGE_CHUNK, model="m", first_token_ms=80, total_ms=200)
    assert (s.prompt_tokens, s.cached_tokens, s.decode_tokens) == (35, 34, 3)
    assert (s.prefill_ms, s.decode_ms, s.first_token_ms, s.total_ms) == (23, 46, 80, 200)


def test_openai_without_usage_keeps_only_what_we_measured():
    s = from_openai({"choices": []}, model="m", total_ms=150)
    assert s.prompt_tokens is None and s.cached_tokens is None and s.total_ms == 150


def test_speed_line():
    s = TurnStats(model="m", decode_tokens=30, decode_ms=2000, first_token_ms=2400)
    assert s.speed_line() == "first word 2.4 s, 15 tokens/s"
    assert TurnStats(model="m", first_token_ms=36).speed_line() == "first word 36 ms"


# -- the stored record cannot hold text --------------------------------------------------------------


def test_turn_stats_holds_numbers_and_the_model_name_only():
    fields = {f.name: f.type for f in dataclasses.fields(TurnStats)}
    assert set(fields) == {"model", *INT_FIELDS}
    assert all(t in ("int | None", "str") for t in fields.values())


def test_a_parsed_payload_never_carries_prompt_or_answer_text():
    payload = {**OLLAMA_COLD, "message": {"role": "assistant", "content": "SECRET ANSWER"}}
    s = from_ollama(payload, model="qwen2.5:3b", num_ctx=4096)
    assert "SECRET" not in json.dumps(s.as_dict())


def test_the_metric_row_gets_no_text_column_beyond_the_existing_ones():
    cols = {c.name for c in QueryMetric.__table__.columns}
    assert set(INT_FIELDS) <= cols
    assert {c.name for c in QueryMetric.__table__.columns if str(c.type).startswith("VARCHAR")} == {
        "node_id",
        "source",
        "model",
    }


# -- Ollama backend: every path fills last_turn ------------------------------------------------------


def _post_returning(payload):
    def _post(url, json=None, timeout=None, **kw):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    return _post


def test_ollama_non_streaming_fills_last_turn(monkeypatch):
    monkeypatch.setattr(httpx, "post", _post_returning(OLLAMA_COLD))
    be = OllamaBackend("http://x", "qwen2.5:3b", num_ctx=4096)
    assert be.last_turn is None
    assert be.chat(MSGS) == "Hello there"
    assert be.last_turn is not None and be.last_turn.cached_tokens == 0
    assert be.last_turn.num_ctx == 4096 and be.last_turn.model == "qwen2.5:3b"
    assert be.last_stats["eval_count"] == 3  # the older record is kept


def test_ollama_tool_path_fills_last_turn(monkeypatch):
    monkeypatch.setattr(httpx, "post", _post_returning(OLLAMA_WARM))
    be = OllamaBackend("http://x", "qwen2.5:3b", num_ctx=4096)
    be.chat_with_tools(MSGS, tools=[])
    assert be.last_turn is not None and be.last_turn.cached_tokens == 34


def test_ollama_failed_call_clears_last_turn(monkeypatch):
    be = OllamaBackend("http://x", "m", num_ctx=4096)
    monkeypatch.setattr(httpx, "post", _post_returning(OLLAMA_COLD))
    be.chat(MSGS)
    monkeypatch.setattr(httpx, "post", _post_returning({"message": {"content": ""}}))
    with pytest.raises(BackendError):
        be.chat(MSGS)
    assert be.last_turn is None


class _FakeStream:
    """What httpx.stream yields: a response with a status and NDJSON/SSE lines."""

    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.is_success = True

    def iter_lines(self):
        yield from self._lines


def _stream_returning(lines):
    @contextlib.contextmanager
    def _stream(method, url, **kw):
        yield _FakeStream(lines)

    return _stream


def _ndjson(*chunks):
    return [json.dumps(c) for c in chunks]


def test_ollama_streaming_fills_last_turn_with_first_token_time(monkeypatch):
    chunks = _ndjson(
        {"message": {"content": "Hello"}, "done": False},
        {"message": {"content": " there"}, "done": False},
        OLLAMA_WARM,
    )
    monkeypatch.setattr(httpx, "stream", _stream_returning(chunks))
    be = OllamaBackend("http://x", "qwen2.5:3b", num_ctx=4096)
    assert "".join(be.chat_stream(MSGS)) == "Hello there"
    s = be.last_turn
    assert s is not None and s.cached_tokens == 34 and s.decode_tokens == 3
    assert s.first_token_ms is not None and s.first_token_ms >= 0


def test_ollama_abandoned_stream_leaves_none(monkeypatch):
    chunks = _ndjson({"message": {"content": "Hello"}, "done": False}, OLLAMA_WARM)
    monkeypatch.setattr(httpx, "stream", _stream_returning(chunks))
    be = OllamaBackend("http://x", "m", num_ctx=4096)
    gen = be.chat_stream(MSGS)
    next(gen)
    gen.close()
    assert be.last_turn is None


def test_ollama_stream_with_no_text_is_an_error_and_leaves_none(monkeypatch):
    monkeypatch.setattr(httpx, "stream", _stream_returning(_ndjson(OLLAMA_WARM)))
    be = OllamaBackend("http://x", "m", num_ctx=4096)
    with pytest.raises(BackendError):
        list(be.chat_stream(MSGS))
    assert be.last_turn is None


# -- OpenAI-compatible backend ----------------------------------------------------------------------


def _chat_completion(usage=None):
    body = {"choices": [{"message": {"content": "Hello there"}, "finish_reason": "stop"}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_openai_non_streaming_reads_usage(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", _post_returning(_chat_completion(OPENAI_USAGE_CHUNK["usage"]))
    )
    be = OpenAICompatBackend("http://x/v1", "m")
    be.chat(MSGS)
    s = be.last_turn
    assert s is not None and (s.prompt_tokens, s.cached_tokens, s.decode_tokens) == (35, 34, 3)
    assert s.total_ms is not None


def test_openai_without_usage_still_records_our_own_total(monkeypatch):
    monkeypatch.setattr(httpx, "post", _post_returning(_chat_completion()))
    be = OpenAICompatBackend("http://x/v1", "m")
    be.chat(MSGS)
    assert be.last_turn is not None
    assert be.last_turn.prompt_tokens is None and be.last_turn.total_ms is not None


def test_openai_streaming_reads_the_usage_chunk_that_has_no_choices(monkeypatch):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: " + json.dumps(OPENAI_USAGE_CHUNK),
        "data: [DONE]",
    ]
    monkeypatch.setattr(httpx, "stream", _stream_returning(lines))
    be = OpenAICompatBackend("http://x/v1", "m")
    assert "".join(be.chat_stream(MSGS)) == "Hello"
    s = be.last_turn
    assert s is not None and s.cached_tokens == 34 and s.prefill_ms == 23
    assert s.first_token_ms is not None


def test_openai_tool_path_fills_last_turn(monkeypatch):
    body = {
        "choices": [{"message": {"content": "done", "tool_calls": None}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    monkeypatch.setattr(httpx, "post", _post_returning(body))
    be = OpenAICompatBackend("http://x/v1", "m")
    be.chat_with_tools(MSGS, tools=[])
    assert be.last_turn is not None and be.last_turn.prompt_tokens == 10
    assert be.last_turn.cached_tokens is None


# -- storage ----------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    create_tables(engine)
    return sessionmaker(bind=engine)()


def test_record_stores_the_counters(db):
    stats = from_ollama(OLLAMA_WARM, model="qwen2.5:3b", num_ctx=4096, first_token_ms=300)
    metrics.record(
        db, org_id=None, node_id="web", cache_hit=False, source="generated",
        duration_ms=900, model="qwen2.5:3b", stats=stats,
    )  # fmt: skip
    row = db.query(QueryMetric).one()
    assert (row.prompt_tokens, row.cached_tokens, row.first_token_ms) == (35, 34, 300)
    assert row.duration_ms == 900 and row.total_ms == 73


def test_record_without_stats_leaves_null_not_zero(db):
    metrics.record(
        db, org_id=None, node_id="web", cache_hit=True, source="cache",
        duration_ms=5, model="m",
    )  # fmt: skip
    row = db.query(QueryMetric).one()
    assert all(getattr(row, f) is None for f in INT_FIELDS)


# -- the chat route: the speed line and the stored row ----------------------------------------------


def test_a_chat_turn_shows_the_speed_line_and_stores_the_counters(tmp_path, monkeypatch):
    from test_web_turn_stream import _client

    import anthill.web.app as app_mod

    client, cid = _client(tmp_path, monkeypatch)

    def fake_ask_stream(ws, question, backend, **kw):
        backend.last_turn = from_ollama(
            OLLAMA_WARM, model="qwen2.5:3b", num_ctx=4096, first_token_ms=300
        )
        yield "a"
        yield "b"

    monkeypatch.setattr("anthill.wiki.ask.ask_stream", fake_ask_stream)
    r = client.get(f"/chat/{cid}/stream", params={"message": "Hello there"})
    assert r.status_code == 200
    assert '"speed": "first word 300 ms, 62 tokens/s"' in r.text
    row = app_mod._SessionFactory().query(QueryMetric).one()
    assert (row.prompt_tokens, row.cached_tokens, row.first_token_ms) == (35, 34, 300)


def test_a_chat_turn_without_stats_shows_no_speed_line(tmp_path, monkeypatch):
    from test_web_turn_stream import _client

    import anthill.web.app as app_mod

    client, cid = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["a", "b"]))
    r = client.get(f"/chat/{cid}/stream", params={"message": "Hello there"})
    assert '"speed"' not in r.text
    row = app_mod._SessionFactory().query(QueryMetric).one()
    assert all(getattr(row, f) is None for f in INT_FIELDS)
