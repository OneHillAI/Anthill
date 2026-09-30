"""Web follow-up stays on the SAME question and keeps conversation memory.

The "redo with web search" path used to search the augmented blob (original question + the previous,
possibly-wrong answer + 'improve this'), so the web results drifted off-topic. Now the web search uses
the clean original question, and the conversation history is threaded through so the follow-up has
memory."""

import anthill.search.web as web
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


class _Backend:
    model = "test"

    def __init__(self):
        self.messages = None

    def chat(self, messages, **kw):
        self.messages = messages
        return "ok"


# ── search uses the clean question, not the redo blob ─────────────────────────


def test_search_uses_clean_query_not_the_augmented_blob(monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "web_search", lambda q, **k: seen.update(q=q) or [])
    augmented = (
        "What is a reverse merger?\n\n[An earlier answer was:]\nSomething about Reddit and LLMs"
    )
    web.search_and_answer(augmented, backend=_Backend(), search_query="What is a reverse merger?")
    assert seen["q"] == "What is a reverse merger?"  # searched the clean question, not the blob


def test_search_query_defaults_to_question(monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "web_search", lambda q, **k: seen.update(q=q) or [])
    web.search_and_answer("plain question", backend=_Backend())  # no search_query
    assert seen["q"] == "plain question"


# ── web sources are cited as real links, not a literal "(URL)" placeholder ────


def test_web_citation_prompt_demands_real_urls_not_a_placeholder(monkeypatch):
    """The system prompt used to say 'cite web sources with (URL)', which small models parroted as
    the literal string (URL). It must now demand the real https:// link and forbid the placeholder."""
    monkeypatch.setattr(
        web,
        "web_search",
        lambda *a, **k: [
            web.SearchResult("Britannica", "https://www.britannica.com/x", "Reykjavik")
        ],
    )
    b = _Backend()
    web.search_and_answer("capital of Iceland?", backend=b)
    system = next(m.content for m in b.messages if m.role == "system")
    assert "(URL)" not in system  # the placeholder instruction is gone
    assert "https://" in system  # it shows a real-link example
    assert "placeholder" in system.lower()  # and explicitly forbids placeholders
    # the actual result URL is handed to the model to cite
    user = b.messages[-1].content
    assert "https://www.britannica.com/x" in user


# ── the web answer carries conversation memory ────────────────────────────────


def test_web_answer_includes_conversation_history(monkeypatch):
    monkeypatch.setattr(web, "web_search", lambda *a, **k: [])
    b = _Backend()
    web.search_and_answer(
        "what's my name?",
        backend=b,
        history=[("user", "my name is Sam"), ("assistant", "Hi Sam")],
    )
    contents = [getattr(m, "content", "") for m in b.messages]
    assert any("my name is Sam" in c for c in contents)  # history reached the web synthesis
    assert b.messages[-1].role == "user" and "what's my name?" in b.messages[-1].content


# ── ask() forwards the clean query + history to the web path ──────────────────


def test_ask_web_path_forwards_clean_query_and_history(tmp_path, monkeypatch):
    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    captured = {}

    def _fake_saa(question, *, backend, **kw):
        captured["question"] = question
        captured["search_query"] = kw.get("search_query")
        captured["history"] = kw.get("history")
        return "web answer"

    monkeypatch.setattr(web, "search_and_answer", _fake_saa)

    ws = Workspace(tmp_path / "w")
    ws.init()
    answer, _slugs, _hit = ask_mod.ask(
        ws,
        "augmented blob to improve",
        _Backend(),
        web_search=True,
        search_query="the clean question",
        history=[("user", "earlier turn")],
    )
    assert answer == "web answer"
    assert captured["search_query"] == "the clean question"
    assert captured["history"] == [("user", "earlier turn")]
