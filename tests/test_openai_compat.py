"""The OpenAI-compatible backend turns transport failures into messages a human can act on -
especially a serverless cold-start timeout and an auth failure, which are the common org-endpoint
problems (a bare 'read operation timed out' tells the user nothing).

Also covers the egress PII scrub (#661): every real network call through this backend - an org's
own RunPod/onprem server, or a third-party inference provider like Berget - must redact PII before
it leaves the machine, and restore it in the reply so the user never sees a raw placeholder. The
on-device loopback endpoint (the local mlx-lm fine-tune server) is exempt, since nothing crosses a
perimeter there."""

import httpx
import pytest

from anthill.inference.base import BackendError, Message, stays_local
from anthill.inference.openai_compat import OpenAICompatBackend, _restore_stream


def test_default_timeout_tolerates_a_cold_start():
    be = OpenAICompatBackend("https://e/v1", "m")
    assert be.timeout == 300.0


def test_timeout_becomes_a_clear_cold_start_message(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _boom)
    be = OpenAICompatBackend("https://api.runpod.ai/v2/x/openai/v1", "m", "key")
    with pytest.raises(BackendError) as ei:
        be.chat([Message("user", "hi")])
    msg = str(ei.value)
    assert "did not respond in time" in msg and "cold-starting" in msg


def test_max_tokens_defaults_to_unset_in_the_payload(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m")
    be.chat([Message("user", "hi")])
    assert "max_tokens" not in captured["payload"]


def test_max_tokens_is_sent_when_configured(monkeypatch):
    """#820: an escalation call with no cap ran far longer than one that capped output, for a
    single-turn answer that has no business being open-ended."""
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", max_tokens=512)
    be.chat([Message("user", "hi")])
    assert captured["payload"]["max_tokens"] == 512


def test_401_says_unauthorized(monkeypatch):
    req = httpx.Request("POST", "https://e/v1/chat/completions")
    resp = httpx.Response(401, text="no token provided", request=req)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError) as ei:
        be.chat([Message("user", "hi")])
    msg = str(ei.value).lower()
    assert "401" in msg and "unauthorized" in msg


def test_streaming_http_error_reads_the_actionable_body(monkeypatch):
    class _LargeError(httpx.SyncByteStream):
        chunks_read = 0

        def __iter__(self):
            for chunk in [b'{"error":"unknown MLX repository"}', *([b"x" * 64] * 100)]:
                self.chunks_read += 1
                yield chunk

    request = httpx.Request("POST", "https://e/v1/chat/completions")
    body = _LargeError()
    response = httpx.Response(404, stream=body, request=request)

    class _Stream:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            response.close()
            return False

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _Stream())
    be = OpenAICompatBackend("https://e/v1", "m", None)

    with pytest.raises(BackendError) as error:
        list(be.chat_stream([Message("user", "hi")]))

    assert "404" in str(error.value)
    assert "unknown MLX repository" in str(error.value)
    assert "ResponseNotRead" not in str(error.value)
    assert body.chunks_read < 101  # the untrusted error body was not buffered in full


def test_streaming_protocol_error_is_actionable(monkeypatch):
    class _Response:
        is_success = True

        def iter_lines(self):
            yield 'data: {"error":{"message":"configured model is unavailable"}}'
            yield "data: [DONE]"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _Stream())
    be = OpenAICompatBackend("https://e/v1", "m", None)

    with pytest.raises(BackendError, match="configured model is unavailable"):
        list(be.chat_stream([Message("user", "hi")]))


def test_streaming_response_requires_a_completion_marker(monkeypatch):
    class _Response:
        is_success = True

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(())

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: _Stream())
    be = OpenAICompatBackend("https://e/v1", "m", None)

    with pytest.raises(BackendError, match="ended before completion"):
        list(be.chat_stream([Message("user", "hi")]))


def test_streaming_surfaces_refusal_and_rejects_completed_empty_response(monkeypatch):
    class _Response:
        is_success = True

        def __init__(self, lines):
            self.lines = lines

        def iter_lines(self):
            yield from self.lines

    class _Stream:
        def __init__(self, lines):
            self.response = _Response(lines)

        def __enter__(self):
            return self.response

        def __exit__(self, *args):
            return False

    be = OpenAICompatBackend("https://e/v1", "m", None)
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *args, **kwargs: _Stream(
            ['data: {"choices":[{"delta":{"refusal":"I cannot help."},"finish_reason":"stop"}]}']
        ),
    )
    assert "".join(be.chat_stream([Message("user", "hi")])) == "I cannot help."

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *args, **kwargs: _Stream(
            ['data: {"choices":[{"delta":{},"finish_reason":"stop"}]}']
        ),
    )
    with pytest.raises(BackendError, match="empty response"):
        list(be.chat_stream([Message("user", "hi")]))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ReadTimeout("timed out"), "cold-starting"),
        (httpx.RemoteProtocolError("peer closed"), "failed while streaming"),
    ],
)
def test_streaming_transport_errors_are_actionable(monkeypatch, error, message):
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    be = OpenAICompatBackend("https://e/v1", "m", None)

    with pytest.raises(BackendError, match=message):
        list(be.chat_stream([Message("user", "hi")]))


def test_connect_error_points_at_the_endpoint(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _boom)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError) as ei:
        be.chat([Message("user", "hi")])
    assert "Can't reach the model endpoint" in str(ei.value)


# ── egress PII scrubbing ──────────────────────────────────────────────────────


def test_stays_local_true_for_ollama_and_loopback():
    assert stays_local("ollama", "http://anything:11434") is True
    assert stays_local("openai", "http://127.0.0.1:8091/v1") is True
    assert stays_local("openai", "http://localhost:8091/v1") is True


def test_stays_local_false_for_a_real_remote_endpoint():
    assert stays_local("openai", "https://api.berget.ai/v1") is False
    assert stays_local("openai", "https://api.runpod.ai/v2/x/openai/v1") is False


def test_chat_scrubs_pii_before_it_leaves_the_machine(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    be.chat([Message("user", "email me at alice@example.com")])
    sent = captured["payload"]["messages"][0]["content"]
    assert "alice@example.com" not in sent
    # message-indexed placeholder (see _scrub_messages) - "_m0" is this message's position
    assert "[EMAIL_1_m0]" in sent


def test_chat_surfaces_a_refusal_when_content_is_null(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": None, "refusal": "No."}}]},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    assert be.chat([Message("user", "hi")]) == "No."


def test_chat_restores_the_placeholder_in_the_reply(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I'll email [EMAIL_1_m0] now."}}]},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    reply = be.chat([Message("user", "email alice@example.com please")])
    assert "alice@example.com" in reply
    assert "[EMAIL_1_m0]" not in reply


def test_two_messages_with_the_same_pii_kind_do_not_clobber_each_others_mapping(monkeypatch):
    # The actual bug this test guards against: wiki context and the user's own question both
    # containing an email (a routine grounded turn, not a rare edge case) used to both scrub to the
    # bare "[EMAIL_1]", so merging their replacement maps silently dropped one of the two mappings -
    # restore() would then put the WRONG person's email into the reply.
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Contacting [EMAIL_1_m0] about [EMAIL_1_m1]."}}]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    reply = be.chat(
        [
            Message("system", "REFERENCE MATERIAL: contact alice@example.com for billing"),
            Message("user", "please also loop in bob@example.com"),
        ]
    )
    assert "alice@example.com" in reply
    assert "bob@example.com" in reply
    assert "[EMAIL_1_m0]" not in reply and "[EMAIL_1_m1]" not in reply


def test_remote_backend_strips_images_and_notes_why(monkeypatch):
    # There is no honest way to redact PII from arbitrary pixel content the way scrub() redacts text -
    # fail closed instead of leaking a raw image to a remote endpoint.
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    be.chat([Message("user", "what's in this?", images=["base64stuff"])])
    sent = captured["payload"]["messages"][0]
    assert "images" not in sent  # never present on the outbound payload for a remote backend
    assert "not sent" in sent["content"]


def test_remote_backend_strips_images_alongside_pii_scrubbing(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    be.chat([Message("user", "email alice@example.com about this", images=["base64stuff"])])
    sent = captured["payload"]["messages"][0]
    assert "images" not in sent
    assert "alice@example.com" not in sent["content"]
    assert "[EMAIL_1_m0]" in sent["content"]
    assert "not sent" in sent["content"]


def test_loopback_endpoint_keeps_images_and_skips_scrubbing(monkeypatch):
    # The local vision pipeline (on-device Ollama) is completely unaffected - nothing crosses a
    # perimeter there, so images must still reach it.
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("http://127.0.0.1:8091/v1", "m", None)
    be.chat([Message("user", "what's in this?", images=["base64stuff"])])
    assert captured["payload"]["messages"][0]["images"] == ["base64stuff"]


def test_loopback_endpoint_skips_scrubbing(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("http://127.0.0.1:8091/v1", "m", None)
    be.chat([Message("user", "email alice@example.com please")])
    assert "alice@example.com" in captured["payload"]["messages"][0]["content"]


def test_chat_with_tools_rejects_an_incomplete_tool_call(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant"},
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError, match="incomplete tool call"):
        be.chat_with_tools([Message("user", "search")], tools=[])


def test_chat_with_tools_surfaces_a_refusal_without_content(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": "I can't help with that.",
                        },
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_tools([Message("user", "do something unsafe")], tools=[])
    assert result["message"]["content"] == "I can't help with that."
    assert result["message"]["tool_calls"] == []


def test_chat_with_tools_rejects_refusal_and_tool_calls_together(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "refusal": "I can't help with that.",
                            "tool_calls": [{"function": {"name": "search", "arguments": "{}"}}],
                        },
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError, match="refusal and tool calls"):
        be.chat_with_tools([Message("user", "search")], tools=[])


def test_chat_with_tools_rejects_empty_truncated_response(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": ""},
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError, match="empty response"):
        be.chat_with_tools([Message("user", "search")], tools=[])


def test_chat_with_tools_rejects_tool_calls_without_arguments(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"tool_calls": [{"function": {"name": "search_wiki"}}]},
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError, match="incomplete tool call"):
        be.chat_with_tools([Message("user", "search")], tools=[])


def test_chat_with_tools_rejects_length_truncated_tool_call(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": None,
                            "tool_calls": [{"function": {"name": "search", "arguments": "{}"}}],
                        },
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    with pytest.raises(BackendError, match="incomplete tool call"):
        be.chat_with_tools([Message("user", "search")], tools=[])


def test_chat_with_tools_scrubs_and_restores_content_only(monkeypatch):
    def _post(url, json, headers, timeout):
        sent = json["messages"][0]["content"]
        assert "alice@example.com" not in sent
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Emailing [EMAIL_1_m0].",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "send_email",
                                        "arguments": '{"to": "[EMAIL_1_m0]"}',
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_tools([Message("user", "email alice@example.com")], tools=[])
    assert result["message"]["content"] == "Emailing alice@example.com."
    # documented scope decision: tool-call arguments are NOT restored (see the code comment)
    assert result["message"]["tool_calls"][0]["function"]["arguments"] == '{"to": "[EMAIL_1_m0]"}'


def test_restore_stream_handles_a_placeholder_split_across_tokens():
    tokens = ["contact ", "[EMAIL", "_1] now"]
    out = "".join(_restore_stream(iter(tokens), {"[EMAIL_1]": "alice@example.com"}))
    assert out == "contact alice@example.com now"


def test_restore_stream_is_a_noop_without_replacements():
    tokens = ["hello ", "world"]
    assert "".join(_restore_stream(iter(tokens), {})) == "hello world"


def test_chat_stream_scrubs_outbound_and_restores_a_split_placeholder(monkeypatch):
    class _FakeStreamResp:
        is_success = True

        def __init__(self, lines):
            self._lines = lines

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield from self._lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _stream(method, url, json, headers, timeout):
        sent = json["messages"][0]["content"]
        assert "alice@example.com" not in sent
        lines = [
            'data: {"choices":[{"delta":{"content":"contact "}}]}',
            'data: {"choices":[{"delta":{"content":"[EMAIL"}}]}',
            'data: {"choices":[{"delta":{"content":"_1_m0] now"}}]}',
            "data: [DONE]",
        ]
        return _FakeStreamResp(lines)

    monkeypatch.setattr(httpx, "stream", _stream)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    out = "".join(be.chat_stream([Message("user", "email alice@example.com please")]))
    assert out == "contact alice@example.com now"
