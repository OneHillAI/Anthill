"""Token streaming: ask_stream (grounded local streaming) + stream_chat (backend capability shim) +
the OpenAI-compatible SSE parser. The dominant `anthill chat` turn streams the local model; these
assert tokens flow through, the grounding still happens, the answer is cached, and a non-streaming
backend degrades to a single chunk.
"""

import httpx
import pytest

import anthill.inference.ollama as ollama_mod
import anthill.inference.openai_compat as oc
from anthill.inference.base import BackendError, Message
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


class _StreamBackend:
    def chat_stream(self, messages, **k):
        yield from ["We ", "use ", "Postgres."]


def test_ask_stream_yields_tokens_grounds_and_caches(tmp_path, monkeypatch):
    # Force the keyword-fallback retrieval path deterministically (the test's own intent per the
    # _Cache comment below: "avoid embedding dependence") - without this, grounding silently rode
    # whatever embedding backend happened to be importable/reachable in the environment this ran in,
    # which is exactly how a real _keyword_fallback regression (2-letter terms dropped outright, so
    # "what db?" against a page titled "DB" found nothing) went unnoticed here.
    from anthill.cache import embedder as emb

    def _boom(text):
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(emb, "embed", _boom)
    monkeypatch.setattr(emb, "available", lambda: False)

    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("DB", "# DB\n\nWe use Postgres.\n")

    stored = {}

    class _Cache:  # avoid embedding dependence; capture what gets cached
        def __init__(self, **k):
            pass

        def store(self, q, a, slugs=None):
            stored[q] = a

        def lookup(self, q):
            return None

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)

    seen = {}
    out = list(
        ask_mod.ask_stream(ws, "what db?", _StreamBackend(), on_context=lambda u: seen.update(u=u))
    )
    assert "".join(out) == "We use Postgres."  # tokens streamed through
    assert "db" in seen["u"]  # on_context fired with the grounding page slug(s)
    assert stored.get("what db?") == "We use Postgres."  # full answer cached for a fast repeat


class _DeflectStreamBackend:
    def chat_stream(self, messages, **k):
        # A "no info" deflection - the kind that must NOT be memoised.
        yield from [
            "I'm unable to access ",
            "external wikis, so I ",
            "don't have that information.",
        ]


def test_ask_stream_skips_caching_a_nonanswer_deflection(tmp_path, monkeypatch):
    """A deflection ("I'm unable to access... I don't have that information") must not be cached -
    otherwise every near-identical question gets the same dead-end served instantly forever."""
    ws = Workspace(tmp_path / "w")
    ws.init()

    stored = {}

    class _Cache:
        def __init__(self, **k):
            pass

        def store(self, q, a, slugs=None):
            stored[q] = a

        def lookup(self, q):
            return None

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    out = list(ask_mod.ask_stream(ws, "what is the refund window?", _DeflectStreamBackend()))
    assert "unable to access" in "".join(out)  # the deflection still streamed to the user
    assert stored == {}  # ... but nothing was cached, so a retry can recompute


def test_is_cacheable_answer_flags_nonanswers_and_keeps_substance():
    ic = ask_mod._is_cacheable_answer
    # non-answers -> not cacheable
    assert ic("") is False
    assert ic("   \n ") is False
    assert ic("I don't have that information.") is False
    assert ic("I'm unable to access external wikis, so I don't have that data.") is False
    assert ic("No relevant wiki pages were found.") is False
    # substantive answers -> cacheable, even a short one
    assert ic("The refund window is 45 days.") is True
    # a long, real answer that merely includes a deflection-shaped caveat is still cached: the
    # length guard means the phrase alone does not disqualify a substantive response.
    long_answer = (
        "The refund policy has several tiers depending on the product line and the region. "
        "For hardware the return window is 30 days from delivery; for software subscriptions it "
        "is 14 days from purchase; annual plans are prorated to the unused months. Refunds are "
        "issued to the original payment method within five business days of approval. For the "
        "enterprise tier I don't have that data to hand because those contracts are negotiated "
        "individually, but the standard consumer windows above are the authoritative defaults."
    )
    assert len(long_answer) > ask_mod._NON_ANSWER_MAX_LEN  # genuinely long
    assert ask_mod._NON_ANSWER.search(long_answer)  # and it does contain a deflection phrase
    assert ic(long_answer) is True  # ... yet it is still cached, because it is long + substantive


def test_stream_chat_uses_chat_stream_when_present():
    class _B:
        def chat_stream(self, messages, **k):
            yield from ["a", "b"]

    assert list(ask_mod.stream_chat(_B(), [], None)) == ["a", "b"]


def test_ask_does_not_route_an_openai_backend_as_ollama(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    ws.init()
    monkeypatch.setattr(ask_mod.emb, "safe_embed", lambda text: None)

    class _Cache:
        def __init__(self, **kwargs):
            pass

        def lookup(self, question):
            return None

        def store(self, *args, **kwargs):
            pass

    class _Router:
        def route(self, *args, **kwargs):
            raise AssertionError("OpenAI-compatible backends must not use TaskRouter")

    def _post(url, *, json, headers, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            request=request,
        )

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    monkeypatch.setattr(oc.httpx, "post", _post)
    backend = oc.OpenAICompatBackend("http://127.0.0.1:11435/v1", "mlx/repository")

    assert ask_mod.ask(ws, "Test", backend, router=_Router())[0] == "ok"


def test_ask_stream_does_not_route_an_openai_backend_as_ollama(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    ws.init()
    monkeypatch.setattr(ask_mod.emb, "safe_embed", lambda text: None)

    class _Router:
        def route(self, *args, **kwargs):
            raise AssertionError("OpenAI-compatible backends must not use TaskRouter")

    class _Response:
        is_success = True

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(oc.httpx, "stream", lambda *args, **kwargs: _Stream())
    backend = oc.OpenAICompatBackend("http://127.0.0.1:11435/v1", "mlx/repository")

    assert list(ask_mod.ask_stream(ws, "Test", backend, router=_Router())) == ["ok"]


def test_stream_chat_never_applies_an_ollama_override_to_openai(monkeypatch):
    sent = {}

    class _Response:
        is_success = True

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    def _stream(method, url, *, json, headers, timeout):
        sent.update(json)
        return _Stream()

    monkeypatch.setattr(oc.httpx, "stream", _stream)
    backend = oc.OpenAICompatBackend("http://127.0.0.1:11435/v1", "mlx/repository")

    assert list(ask_mod.stream_chat(backend, [], "qwen2.5:3b")) == ["ok"]
    assert sent["model"] == "mlx/repository"


def test_stream_chat_falls_back_to_whole_answer_when_not_streamable():
    class _B:
        def chat(self, messages, **k):
            return "whole answer"

    assert list(ask_mod.stream_chat(_B(), [], None)) == ["whole answer"]


def test_ollama_streaming_http_error_reads_the_actionable_body(monkeypatch):
    class _LargeError(httpx.SyncByteStream):
        chunks_read = 0

        def __iter__(self):
            for chunk in [b'{"error":"model is not installed"}', *([b"x" * 64] * 100)]:
                self.chunks_read += 1
                yield chunk

    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    body = _LargeError()
    response = httpx.Response(404, stream=body, request=request)

    class _Stream:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            response.close()
            return False

    monkeypatch.setattr(ollama_mod.httpx, "stream", lambda *args, **kwargs: _Stream())
    backend = ollama_mod.OllamaBackend("http://localhost:11434", "missing")

    with pytest.raises(BackendError) as error:
        list(backend.chat_stream([Message("user", "hi")]))

    assert "404" in str(error.value)
    assert "model is not installed" in str(error.value)
    assert "ResponseNotRead" not in str(error.value)
    assert body.chunks_read < 101  # the untrusted error body was not buffered in full


def test_ollama_streaming_response_requires_done_marker(monkeypatch):
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

    monkeypatch.setattr(ollama_mod.httpx, "stream", lambda *args, **kwargs: _Stream())
    backend = ollama_mod.OllamaBackend("http://localhost:11434", "m")

    with pytest.raises(BackendError, match="ended before completion"):
        list(backend.chat_stream([Message("user", "hi")]))


def test_ollama_stream_rejects_completed_empty_response(monkeypatch):
    class _Response:
        is_success = True

        def iter_lines(self):
            yield '{"message":{"content":""},"done":true}'

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ollama_mod.httpx, "stream", lambda *args, **kwargs: _Stream())
    backend = ollama_mod.OllamaBackend("http://localhost:11434", "m")

    with pytest.raises(BackendError, match="empty response"):
        list(backend.chat_stream([Message("user", "hi")]))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ReadTimeout("timed out"), "still be loading"),
        (httpx.RemoteProtocolError("peer closed"), "failed while streaming"),
    ],
)
def test_ollama_streaming_transport_errors_are_actionable(monkeypatch, error, message):
    monkeypatch.setattr(
        ollama_mod.httpx,
        "stream",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    backend = ollama_mod.OllamaBackend("http://localhost:11434", "m")

    with pytest.raises(BackendError, match=message):
        list(backend.chat_stream([Message("user", "hi")]))


def test_openai_compat_chat_stream_parses_sse(monkeypatch):
    class _Resp:
        is_success = True

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"Hel"}}]}'
            yield ""  # keep-alive blank line, ignored
            yield 'data: {"choices":[{"delta":{"content":"lo"}}]}'
            yield "data: [DONE]"

    class _Ctx:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(oc.httpx, "stream", lambda *a, **k: _Ctx())
    backend = oc.OpenAICompatBackend("http://x/v1", "m")
    assert "".join(backend.chat_stream([])) == "Hello"
