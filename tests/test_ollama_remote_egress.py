"""An Ollama on another machine gets the same egress protection as any other network endpoint (#245): the text is
redacted before it is sent, images are dropped, and the reply is restored. An Ollama on this machine is untouched.

Model-free: every call is a fake, and the placeholders the model "sees" are read back out of the request body."""

import contextlib
import json
import re

import httpx
import pytest

from anthill.inference.base import Message
from anthill.inference.egress import restore_stream, scrub_messages
from anthill.inference.ollama import OllamaBackend

REMOTE = "http://192.168.1.20:11434"
LOCAL = "http://localhost:11434"
EMAIL = "alice@example.com"
OTHER = "bob@example.org"
MSGS = [
    Message("system", "You are an assistant."),
    Message("user", f"Please mail {EMAIL} today."),
]

# conftest stubs these for every test (no live model); keep the real ones to restore here.
_REAL = {n: getattr(OllamaBackend, n) for n in ("chat", "chat_with_tools", "chat_with_confidence")}


@pytest.fixture(autouse=True)
def _real_methods(monkeypatch):
    for name, fn in _REAL.items():
        monkeypatch.setattr(OllamaBackend, name, fn)


def _answer(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}, "done": True, "eval_count": 3}


class Wire:
    """Records what is sent and answers with the placeholder it was given."""

    def __init__(self, monkeypatch, template="Write to {ph} now."):
        self.sent: list[dict] = []
        self.template = template
        monkeypatch.setattr(httpx, "post", self._post)
        monkeypatch.setattr(httpx, "stream", self._stream)

    def _post(self, url, json=None, timeout=None, **kw):
        self.sent.append(json)
        body = self.template.format(ph=_find(json))
        return httpx.Response(200, json=_answer(body), request=httpx.Request("POST", url))

    @contextlib.contextmanager
    def _stream(self, method, url, json=None, timeout=None, **kw):
        self.sent.append(json)
        text = self.template.format(ph=_find(json))
        cut = len(text) // 2  # split mid-placeholder when there is one
        lines = [
            _line({"message": {"content": text[:cut]}, "done": False}),
            _line({"message": {"content": text[cut:]}, "done": False}),
            _line({"message": {"content": ""}, "done": True, "eval_count": 3}),
        ]
        yield _Resp(lines)


def _find(payload: dict) -> str:
    found = re.search(r"\[EMAIL_\d+_m\d+\]", json.dumps(payload))
    return found.group(0) if found else "(none)"


def _line(obj):
    return json.dumps(obj)


class _Resp:
    is_success, status_code = True, 200

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        yield from self._lines


def _backend(url):
    return OllamaBackend(url, "m", num_ctx=4096)


# -- the decision ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "scrubbed"),
    [
        (REMOTE, True),
        ("http://gpu-box.local:11434", True),
        (LOCAL, False),
        ("http://host.docker.internal:11434", False),
    ],
)
def test_only_an_ollama_on_another_machine_is_protected(url, scrubbed):
    assert _backend(url)._scrub_egress is scrubbed


# -- a remote Ollama: redacted out, restored back ----------------------------------------------------


def test_remote_chat_sends_no_email_and_returns_it_restored(monkeypatch):
    wire = Wire(monkeypatch)
    assert _backend(REMOTE).chat(MSGS) == f"Write to {EMAIL} now."
    body = json.dumps(wire.sent[0])
    assert EMAIL not in body and "[EMAIL_1_m1]" in body


def test_remote_chat_with_confidence_is_protected_too(monkeypatch):
    wire = Wire(monkeypatch)
    result = _backend(REMOTE).chat_with_confidence(MSGS)
    assert result.text == f"Write to {EMAIL} now." and EMAIL not in json.dumps(wire.sent[0])


def test_remote_stream_restores_a_placeholder_split_across_chunks(monkeypatch):
    wire = Wire(monkeypatch, template="Write to {ph} now.")
    tokens = list(_backend(REMOTE).chat_stream(MSGS))
    assert "".join(tokens) == f"Write to {EMAIL} now."
    assert not any(
        "[EMAIL" in t and "]" not in t for t in tokens
    )  # never a half-restored placeholder
    assert EMAIL not in json.dumps(wire.sent[0])


def test_remote_tools_restore_the_text_but_not_the_arguments(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append(json)
        ph = _find(json)
        reply = {
            "message": {
                "content": f"Sending to {ph}.",
                "tool_calls": [{"function": {"name": "send", "arguments": {"to": ph}}}],
            },
            "done": True,
        }
        return httpx.Response(200, json=reply, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    reply = _backend(REMOTE).chat_with_tools(MSGS, tools=[])
    assert reply["message"]["content"] == f"Sending to {EMAIL}."
    # The model only ever saw placeholders: an argument is left as it came, never restored inside JSON.
    assert reply["message"]["tool_calls"][0]["function"]["arguments"]["to"] == "[EMAIL_1_m1]"
    assert EMAIL not in json.dumps(sent[0])


def test_two_different_emails_in_two_messages_do_not_clobber_each_other(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append(json)
        return httpx.Response(
            200, json=_answer("A [EMAIL_1_m0] B [EMAIL_1_m1]"), request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    out = _backend(REMOTE).chat(
        [Message("user", f"context: {EMAIL}"), Message("user", f"question: {OTHER}")]
    )
    assert out == f"A {EMAIL} B {OTHER}"


def test_a_remote_ollama_gets_no_image_and_the_reply_says_why(monkeypatch):
    wire = Wire(monkeypatch)
    _backend(REMOTE).chat([Message("user", f"what is this? {EMAIL}", images=["QUJD"])])
    message = wire.sent[0]["messages"][0]
    assert (
        "images" not in message
        and "not sent" in message["content"]
        and "QUJD" not in json.dumps(wire.sent[0])
    )


# -- a local Ollama: exactly as before ---------------------------------------------------------------


def test_a_local_ollama_is_sent_the_text_as_written_and_keeps_its_images(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append(json)
        return httpx.Response(
            200, json=_answer(f"Reply mentions {EMAIL}."), request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    out = _backend(LOCAL).chat([Message("user", f"mail {EMAIL}", images=["QUJD"])])
    assert out == f"Reply mentions {EMAIL}."
    message = sent[0]["messages"][0]
    assert (
        EMAIL in message["content"]
        and message["images"] == ["QUJD"]
        and "[EMAIL" not in json.dumps(sent[0])
    )


def test_a_local_stream_passes_tokens_through_untouched(monkeypatch):
    Wire(monkeypatch, template="Hello there.")
    assert "".join(_backend(LOCAL).chat_stream(MSGS)) == "Hello there."


# -- the shared helpers ------------------------------------------------------------------------------


def test_the_shared_helpers_are_the_ones_the_openai_backend_uses():
    from anthill.inference import openai_compat

    assert (
        openai_compat._scrub_messages is scrub_messages
        and openai_compat._restore_stream is restore_stream
    )


# -- through the chat route --------------------------------------------------------------------------


def _route(tmp_path, monkeypatch, url):
    from test_chat_stream_cancellation import _client

    from anthill.web.db import OrgSettings

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    session.add(OrgSettings(org_id=1, ollama_url=url))
    session.commit()
    wire = Wire(monkeypatch)

    def ask_stream(ws, question, backend, **kw):
        yield from backend.chat_stream([Message("user", f"Please mail {EMAIL} today.")])

    monkeypatch.setattr("anthill.wiki.ask.ask_stream", ask_stream)
    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})
    assert response.status_code == 200
    return response.text, wire


def test_a_lan_ollama_through_the_chat_route_is_remote_and_sent_no_email(tmp_path, monkeypatch):
    text, wire = _route(tmp_path, monkeypatch, REMOTE)
    assert '"answered_locally": false' in text
    assert EMAIL not in json.dumps(wire.sent[0])
    assert EMAIL in text  # the user still sees their own address in the answer


def test_the_default_local_ollama_through_the_chat_route_is_sent_the_text_as_written(
    tmp_path, monkeypatch
):
    text, wire = _route(tmp_path, monkeypatch, LOCAL)
    assert '"answered_locally": true' in text
    assert EMAIL in json.dumps(wire.sent[0])
