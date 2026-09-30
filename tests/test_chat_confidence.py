"""InferenceBackend.chat_with_confidence() (#278 stronger-escalation work): a small result object
(ChatResult) carrying the answer text plus an optional logprob-derived confidence signal, alongside
the existing chat() -> str contract (left completely unchanged so the ~20 existing call sites across
the codebase need no changes). Confidence parsing is defensive everywhere: a missing/null/malformed
logprobs field degrades to confidence=None rather than raising, since this is a bonus signal never
load-bearing for the base chat() path.
"""

import httpx
import pytest

from anthill.inference.base import ChatResult, Message, _mean_logprob
from anthill.inference.mlx_local import MlxBackend
from anthill.inference.ollama import OllamaBackend
from anthill.inference.openai_compat import OpenAICompatBackend

# tests/conftest.py's autouse _no_live_ollama fixture stubs OllamaBackend.chat_with_confidence to
# always raise (same hermetic-default treatment as chat()/chat_with_tools()) so OTHER tests can't
# accidentally reach a live local Ollama through it once it's wired into the escalation trigger.
# Captured here, at collection time (before that fixture ever runs), so tests that actually want to
# exercise the real HTTP-calling implementation (with httpx itself mocked) can restore it.
_REAL_OLLAMA_CHAT_WITH_CONFIDENCE = OllamaBackend.chat_with_confidence

# ── _mean_logprob (base.py) ─────────────────────────────────────────────────────────────


def test_mean_logprob_averages_the_logprob_field():
    tokens = [{"logprob": -0.1}, {"logprob": -0.3}, {"logprob": -0.2}]
    assert _mean_logprob(tokens) == pytest.approx(-0.2)


def test_mean_logprob_none_on_empty_list():
    assert _mean_logprob([]) is None


def test_mean_logprob_none_on_missing_field():
    assert _mean_logprob([{"token": "hi"}]) is None


def test_mean_logprob_none_on_wrong_shape():
    assert _mean_logprob("not a list") is None  # type: ignore[arg-type]
    assert _mean_logprob([{"logprob": "not a number"}]) is None


# ── OpenAICompatBackend.chat_with_confidence ────────────────────────────────────────────


def test_openai_compat_confidence_requests_logprobs_fields(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "ok"},
                        "logprobs": {"content": [{"token": "ok", "logprob": -0.05}]},
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_confidence([Message("user", "hi")])
    assert captured["payload"]["logprobs"] is True
    assert captured["payload"]["top_logprobs"] == 1
    assert result == ChatResult(text="ok", confidence=-0.05)


def test_openai_compat_plain_chat_does_not_request_logprobs(monkeypatch):
    captured = {}

    def _post(url, json, headers, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    be.chat([Message("user", "hi")])
    assert "logprobs" not in captured["payload"]
    assert "top_logprobs" not in captured["payload"]


def test_openai_compat_confidence_none_when_provider_ignores_logprobs(monkeypatch):
    # Groq: documents logprobs/top_logprobs as accepted-but-not-implemented - the response simply
    # has no logprobs field, exactly like this.
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_confidence([Message("user", "hi")])
    assert result == ChatResult(text="ok", confidence=None)


def test_openai_compat_confidence_none_on_null_logprobs_field(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "logprobs": None}]},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_confidence([Message("user", "hi")])
    assert result == ChatResult(text="ok", confidence=None)


def test_openai_compat_confidence_still_restores_pii_placeholder(monkeypatch):
    def _post(url, json, headers, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "I'll email [EMAIL_1_m0] now."},
                        "logprobs": {"content": [{"logprob": -0.02}]},
                    }
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OpenAICompatBackend("https://e/v1", "m", None)
    result = be.chat_with_confidence([Message("user", "email alice@example.com please")])
    assert "alice@example.com" in result.text
    assert "[EMAIL_1_m0]" not in result.text
    assert result.confidence == -0.02


# ── OllamaBackend.chat_with_confidence ───────────────────────────────────────────────────


def test_ollama_payload_logprobs_flag_only_set_when_requested():
    # Unit-level, matching test_inference_opt.py's existing _payload() convention - avoids the
    # hermetic-default fixture entirely (it stubs chat()/chat_with_confidence(), not _payload()).
    be = OllamaBackend("http://x", "m")
    plain = be._payload([Message("user", "hi")], temperature=0.2, model=None, stream=False)
    assert "logprobs" not in plain
    confident = be._payload(
        [Message("user", "hi")], temperature=0.2, model=None, stream=False, logprobs=True
    )
    assert confident["logprobs"] is True


def test_ollama_confidence_requests_logprobs_field(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_confidence", _REAL_OLLAMA_CHAT_WITH_CONFIDENCE)
    captured = {}

    def _post(url, json, timeout):
        captured["payload"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "ok",
                    "logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.3}]},
                }
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OllamaBackend("http://x", "m")
    result = be.chat_with_confidence([Message("user", "hi")])
    assert captured["payload"]["logprobs"] is True
    assert result == ChatResult(text="ok", confidence=pytest.approx(-0.2))


def test_ollama_confidence_none_on_older_install_that_ignores_the_field(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_confidence", _REAL_OLLAMA_CHAT_WITH_CONFIDENCE)

    def _post(url, json, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": {"content": "ok"}}, request=req)

    monkeypatch.setattr(httpx, "post", _post)
    be = OllamaBackend("http://x", "m")
    result = be.chat_with_confidence([Message("user", "hi")])
    assert result == ChatResult(text="ok", confidence=None)


def test_ollama_confidence_none_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_confidence", _REAL_OLLAMA_CHAT_WITH_CONFIDENCE)

    def _post(url, json, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"message": {"content": "ok", "logprobs": "not a dict"}},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OllamaBackend("http://x", "m")
    result = be.chat_with_confidence([Message("user", "hi")])
    assert result == ChatResult(text="ok", confidence=None)


def test_ollama_confidence_still_populates_last_stats(monkeypatch):
    monkeypatch.setattr(OllamaBackend, "chat_with_confidence", _REAL_OLLAMA_CHAT_WITH_CONFIDENCE)

    def _post(url, json, timeout):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "eval_count": 42},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", _post)
    be = OllamaBackend("http://x", "m")
    be.chat_with_confidence([Message("user", "hi")])
    assert be.last_stats["eval_count"] == 42


# ── MlxBackend.chat_with_confidence (Protocol conformance only) ────────────────────────────


def test_mlx_backend_confidence_wraps_plain_chat(monkeypatch):
    be = MlxBackend("mlx-community/x")
    monkeypatch.setattr(be, "chat", lambda messages, **kw: "answer text")
    result = be.chat_with_confidence([Message("user", "hi")])
    assert result == ChatResult(text="answer text", confidence=None)
