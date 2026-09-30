"""Streaming research (chat-hang part 2 real fix, #332 follow-up). The research chat branch now streams
the cited report AS it is produced - a status line (immediate bytes), then the synthesized body
token-by-token, then the Sources - instead of awaiting the whole report and word-splitting it. Synthesis
runs with thinking OFF and bounded. Fixes the zero-byte ReadTimeout on research_exec_sources."""

import pytest

from anthill import research
from anthill.inference.ollama import OllamaBackend


class _Src:
    def __init__(self, i):
        self.url = f"http://example/{i}"
        self.title = f"Source {i}"
        self.body = "Solid-state batteries use a solid electrolyte. " * 10
        self.snippet = "overview"


def _search(query, n):
    return [_Src(1), _Src(2)]


class _Streamer:
    def __init__(self):
        self.kwargs = None

    def chat_stream(self, messages, *, num_predict=None, think=None):
        self.kwargs = {"num_predict": num_predict, "think": think}
        yield "Solid-state batteries "
        yield "are promising."


def test_research_stream_emits_status_body_and_sources():
    out = "".join(
        research.research_stream("solid-state batteries", backend=_Streamer(), search_fn=_search)
    )
    assert "Searching the web" in out  # status arrives (real TTFT, no zero-byte window)
    assert "Read 2 sources" in out
    assert "Solid-state batteries are promising." in out  # streamed body
    assert "## Sources" in out and "http://example/1" in out and "http://example/2" in out


def test_research_synthesis_streams_with_thinking_off_and_bounded():
    be = _Streamer()
    list(research.research_stream("topic", backend=be, search_fn=_search))
    assert be.kwargs == {"num_predict": research.RESEARCH_MAX_TOKENS, "think": False}


def test_research_stream_raises_on_no_sources():
    with pytest.raises(research.ResearchError):
        list(research.research_stream("topic", backend=_Streamer(), search_fn=lambda q, n: []))


def test_research_stream_falls_back_for_a_nonstreaming_backend():
    class _Blocking:
        def chat(self, messages, **k):
            return "a full report body"

    out = "".join(research.research_stream("topic", backend=_Blocking(), search_fn=_search))
    assert "a full report body" in out and "## Sources" in out


def test_ollama_payload_think_field():
    be = OllamaBackend("http://x", "m")
    p = be._payload([], temperature=0.2, model=None, stream=True, think=False)
    assert p["think"] is False
    assert "think" not in be._payload([], temperature=0.2, model=None, stream=True)
