"""Discover the models a self-hosted / VPC endpoint actually serves, however it was stood up."""

import httpx
import pytest

from anthill.hosting import endpoint as ep


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


def _patch_get(monkeypatch, routes):
    """Route httpx.get by URL to a canned response; a missing URL 404s (server doesn't serve it)."""

    def fake_get(url, **kwargs):
        if url in routes:
            return _Resp(routes[url])
        return _Resp({}, status=404)

    monkeypatch.setattr(httpx, "get", fake_get)


def test_openai_models_list_is_sorted_and_deduped(monkeypatch):
    _patch_get(
        monkeypatch,
        {"http://box:8000/v1/models": {"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}},
    )
    assert ep.list_models("http://box:8000/v1") == ["a", "b"]


def test_falls_back_to_v1_when_base_omits_the_suffix(monkeypatch):
    # a Mac Mini pasted as host:11434 (no /v1): {base}/models 404s, {base}/v1/models works (Ollama's /v1)
    _patch_get(
        monkeypatch,
        {"http://mac-mini:11434/v1/models": {"data": [{"id": "llama3.1:8b"}]}},
    )
    assert ep.list_models("http://mac-mini:11434") == ["llama3.1:8b"]


def test_falls_back_to_ollama_native_api_tags(monkeypatch):
    # neither OpenAI-compatible path serves models; the box exposes only /api/tags
    _patch_get(
        monkeypatch,
        {"http://mac-mini:11434/api/tags": {"models": [{"name": "qwen2.5:14b"}, {"name": "phi3"}]}},
    )
    assert ep.list_models("http://mac-mini:11434") == ["phi3", "qwen2.5:14b"]


def test_sends_bearer_key_when_provided(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen[url] = kwargs.get("headers", {})
        return _Resp({"data": [{"id": "m"}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    ep.list_models("https://gpu.example/v1", api_key="secret")
    assert seen["https://gpu.example/v1/models"]["Authorization"] == "Bearer secret"


def test_raises_when_the_server_is_unreachable(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(httpx.HTTPError):
        ep.list_models("http://down:9999/v1")
