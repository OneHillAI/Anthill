"""json_chat bounds generation on structured/classification model calls (server chat-hang finding,
qa/chat-eval/DEV_FINDINGS.md 2026-07-08). A reasoning model under a forced-JSON grammar can generate
until the context fills; a token cap turns that runaway into a bounded result. The cap degrades
gracefully for backends that accept fewer kwargs, and never truncates a (small) JSON reply."""

from anthill.common.jsonchat import JSON_MAX_TOKENS, json_chat
from anthill.inference.ollama import OllamaBackend


class _FullBackend:
    """Accepts fmt + num_predict (the Ollama shape)."""

    def __init__(self):
        self.kwargs = None

    def chat(self, messages, *, fmt="", num_predict=None):
        self.kwargs = {"fmt": fmt, "num_predict": num_predict}
        return '{"ok": true}'


class _FmtOnlyBackend:
    """Accepts fmt but not num_predict -> the first attempt TypeErrors, json_chat drops num_predict."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, fmt=""):
        self.calls += 1
        return '{"ok": true}'


class _PlainBackend:
    """Accepts neither kwarg -> json_chat degrades all the way to a plain call."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return '{"ok": true}'


def test_json_chat_caps_generation_on_a_capable_backend():
    be = _FullBackend()
    assert json_chat(be, []) == '{"ok": true}'
    assert be.kwargs == {"fmt": "json", "num_predict": JSON_MAX_TOKENS}  # bounded + JSON-forced


def test_json_chat_drops_num_predict_when_unsupported():
    be = _FmtOnlyBackend()
    assert json_chat(be, []) == '{"ok": true}'  # falls back to fmt-only, still works


def test_json_chat_degrades_to_a_plain_call():
    be = _PlainBackend()
    assert json_chat(be, []) == '{"ok": true}'  # falls back all the way to chat(messages)


def test_ollama_payload_includes_num_predict_only_when_set():
    be = OllamaBackend("http://x", "m")
    with_cap = be._payload([], temperature=0.2, model=None, stream=False, num_predict=256)
    assert with_cap["options"]["num_predict"] == 256
    without = be._payload([], temperature=0.2, model=None, stream=False)
    assert "num_predict" not in without["options"]  # unbounded by default (free-form generation)
