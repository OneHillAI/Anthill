"""Conversation history reaches the model (the chat handler used to fetch the last turns and throw
them away), and heavy histories are reconciled to the context budget."""

from anthill.wiki import ask as ask_mod
from anthill.wiki import prompts
from anthill.wiki.history import prepare_history
from anthill.wiki.workspace import Workspace

# ── prepare_history: budget + reconciliation ──────────────────────────────────


def test_history_under_budget_passes_through():
    turns = [("user", "hi"), ("assistant", "hello")]
    assert prepare_history(turns, budget_chars=1000) == turns


def test_empty_history_is_empty():
    assert prepare_history([]) == []
    assert prepare_history([("user", "  ")]) == []  # blank turns dropped


def test_over_budget_summarizes_old_keeps_recent():
    turns = [
        ("user", "A" * 40),
        ("assistant", "B" * 40),
        ("user", "C" * 40),
        ("assistant", "keep me"),
    ]
    out = prepare_history(turns, budget_chars=50, keep_last=1, summarize=lambda t: "DIGEST")
    assert out[0][0] == "system" and "DIGEST" in out[0][1]  # older turns condensed
    assert out[-1] == ("assistant", "keep me")  # most recent kept verbatim
    assert len(out) == 2


def test_over_budget_without_summarizer_drops_oldest_to_fit():
    turns = [("user", "X" * 40), ("assistant", "Y" * 40), ("user", "newest")]
    out = prepare_history(turns, budget_chars=50, keep_last=1)  # no summarize
    assert ("user", "newest") in out
    assert all(r != "system" for r, _ in out)  # no summary note
    assert sum(len(c) for _, c in out) <= 50


# ── prompt interleaves history as real turns ──────────────────────────────────


def test_answer_question_interleaves_history():
    history = [("user", "my name is Sam"), ("assistant", "Nice to meet you, Sam")]
    msgs = prompts.answer_question("(the wiki is empty)", "what's my name?", history=history)
    roles = [m.role for m in msgs]
    assert roles[0] == "system"  # system first
    # prefix-stable RAG: the reference block sits up front (right after system), before the conversation
    assert msgs[1].role == "user" and "REFERENCE MATERIAL" in msgs[1].content
    assert (msgs[2].role, msgs[2].content) == ("user", "my name is Sam")
    assert (msgs[3].role, msgs[3].content) == ("assistant", "Nice to meet you, Sam")
    assert msgs[-1].role == "user" and "what's my name?" in msgs[-1].content  # question last


def test_answer_question_is_system_then_reference_then_question():
    # prefix-stable RAG: system, the reference block up front, then the question last (no history)
    msgs = prompts.answer_question("ctx", "q")
    assert [m.role for m in msgs] == ["system", "user", "user"]
    assert "REFERENCE MATERIAL" in msgs[1].content and "ctx" in msgs[1].content
    assert msgs[-1].content == "QUESTION: q"


def test_answer_question_omits_context_block_when_empty():
    # No wiki context -> no "(the wiki is empty)" noise for the model to parrot back.
    msgs = prompts.answer_question("", "what is a reverse merger?")
    assert [m.role for m in msgs] == ["system", "user"]
    assert "CONTEXT" not in msgs[-1].content and "wiki" not in msgs[-1].content.lower()
    assert "what is a reverse merger?" in msgs[-1].content


# ── ask() actually sends the history to the model ─────────────────────────────


def test_ask_sends_prior_turns_to_the_model(tmp_path, monkeypatch):
    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)
    grabbed = {}

    class _Backend:
        model = "test"

        def chat(self, messages, **k):
            grabbed["t"] = "\n".join(getattr(m, "content", "") for m in messages)
            return "ok"

    ws = Workspace(tmp_path / "w")
    ws.init()
    history = [("user", "my name is Sam"), ("assistant", "Hi Sam")]
    ask_mod.ask(ws, "what's my name?", _Backend(), history=history)
    assert "my name is Sam" in grabbed["t"] and "Hi Sam" in grabbed["t"]  # memory reached the model


# ── the /chat stream actually forwards prior turns (the bug was a discarded slice) ──


def test_chat_stream_passes_prior_turns_to_ask(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    import anthill.wiki.ask as wiki_ask
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import ChatMessage, Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo")])
    s.flush()
    conv = Conversation(user_id=u.id, org_id=o.id, plane="solo", title="t")
    s.add(conv)
    s.flush()
    s.add_all(
        [
            ChatMessage(conversation_id=conv.id, role="user", content="my name is Sam"),
            ChatMessage(conversation_id=conv.id, role="assistant", content="Hi Sam"),
        ]
    )
    s.commit()
    conv_id = conv.id

    captured = {}

    def _fake_ask_stream(ws, message, backend, **kw):
        # the plain local-generate turn now streams real tokens; prior turns must still reach it
        captured["history"] = kw.get("history")
        yield "ok"

    monkeypatch.setattr(wiki_ask, "ask_stream", _fake_ask_stream)

    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    r = c.get(f"/chat/{conv_id}/stream", params={"message": "what is my name?"})
    assert r.status_code == 200
    assert captured["history"] == [("user", "my name is Sam"), ("assistant", "Hi Sam")]


# ── a message spoken TO the assistant is never web-searched (the reverse-merger bug) ──


def _solo_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo")])
    s.flush()
    conv = Conversation(user_id=u.id, org_id=o.id, plane="solo", title="t")
    s.add(conv)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, conv.id


def test_conversational_turn_does_not_web_search_even_with_web_on(tmp_path, monkeypatch):
    import anthill.wiki.ask as wiki_ask

    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)
    seen = {"web": None}
    monkeypatch.setattr(
        wiki_ask,
        "ask",
        lambda ws, m, b, **kw: seen.update(web=kw.get("web_search")) or ("ok", [], False),
    )

    def _fs(ws, m, b, **kw):  # the conversational turn takes the streaming local-generate path
        seen["streamed"] = True
        yield "ok"

    monkeypatch.setattr(wiki_ask, "ask_stream", _fs)
    # web explicitly ON, but the message is talking TO the assistant -> must NOT search the web.
    c.get(
        f"/chat/{conv_id}/stream",
        params={"message": "why didn't you tell me that before?", "web": "true"},
    )
    # a web search happens only if ask() is called with web_search=True; it must not be
    assert seen["web"] is not True


def test_real_question_still_web_searches_when_web_on(tmp_path, monkeypatch):
    import anthill.wiki.ask as wiki_ask

    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(
        wiki_ask,
        "ask",
        lambda ws, m, b, **kw: seen.update(web=kw.get("web_search")) or ("ok", [], False),
    )
    c.get(
        f"/chat/{conv_id}/stream",
        params={
            "message": "what are the SEC rules for marketing before a reverse merger",
            "web": "true",
        },
    )
    assert seen["web"] is True  # a genuine topic still uses the web


# ── image attach: a screenshot/photo becomes a vision turn ────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def test_chat_attach_stores_an_image_and_returns_a_one_time_token(tmp_path, monkeypatch):
    import base64

    c, app_mod, conv_id = _solo_client(tmp_path, monkeypatch)
    r = c.post(f"/chat/{conv_id}/attach", files={"file": ("shot.png", _PNG, "image/png")})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token
    assert base64.b64decode(app_mod._pop_pending_image(token)) == _PNG
    assert app_mod._pop_pending_image(token) is None  # one-time: consumed by the turn


def test_chat_attach_rejects_a_non_image(tmp_path, monkeypatch):
    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)
    r = c.post(f"/chat/{conv_id}/attach", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_image_turn_reaches_the_vision_path_and_skips_the_proposal(tmp_path, monkeypatch):
    import anthill.wiki.ask as wiki_ask

    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(
        wiki_ask,
        "ask",
        lambda ws, m, b, **kw: (
            seen.update(images=kw.get("images_b64"), web=kw.get("web_search"))
            or ("that's a bar chart", [], False)
        ),
    )
    token = c.post(f"/chat/{conv_id}/attach", files={"file": ("s.png", _PNG, "image/png")}).json()[
        "token"
    ]
    # "make a summary..." is normally an actionable 'do' proposal; with an image it must instead go
    # straight to the vision answer path (a question ABOUT the image).
    r = c.get(
        f"/chat/{conv_id}/stream",
        params={"message": "make a summary of this", "image_token": token},
    )
    assert r.status_code == 200
    assert seen.get("images") and len(seen["images"]) == 1  # the image reached ask()
    assert seen.get("web") is False  # no web search on an image turn
    assert '"proposal"' not in r.text  # answered the image, never proposed an artifact


# ── deep-research intent: proposal then a cited report ────────────────────────


def test_deep_research_ask_emits_a_research_proposal(tmp_path, monkeypatch):
    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)
    r = c.get(f"/chat/{conv_id}/stream", params={"message": "do deep research on the EU AI Act"})
    assert r.status_code == 200
    assert '"kind": "research"' in r.text  # offered as a research run, not answered inline


def test_confirmed_research_streams_the_report(tmp_path, monkeypatch):
    import anthill.research as research_mod

    c, _app, conv_id = _solo_client(tmp_path, monkeypatch)

    def _fake_research_stream(topic, **k):  # the branch now consumes the streaming generator
        yield "# EU AI Act\n\n"
        yield "Key findings here.\n\n"
        yield "## Sources\n- [gov](https://example.gov)"

    monkeypatch.setattr(research_mod, "research_stream", _fake_research_stream)
    r = c.get(
        f"/chat/{conv_id}/stream",
        params={"message": "the EU AI Act", "research": "true", "confirm": "true"},
    )
    assert r.status_code == 200
    assert "findings" in r.text  # the report body streamed
    assert "Sources" in r.text and "example.gov" in r.text  # the citations streamed


def test_chat_shows_fallback_banner_only_for_a_small_model(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    c, app_mod, conv_id = _solo_client(tmp_path, monkeypatch)  # default model is qwen2.5:3b (small)
    body = c.get(f"/chat/{conv_id}").text
    # banner shown for a small model - generic wording, no model name leaked
    assert "too small" in body and "action-style search" in body
    assert "qwen2.5:3b" not in body

    s = app_mod._SessionFactory()
    s.query(OrgSettings).first().ollama_model = "mistral-nemo:12b"  # a capable model
    s.commit()
    body2 = c.get(f"/chat/{conv_id}").text
    assert "too small" not in body2  # no banner when the model can plan


def test_answer_actions_drop_the_redundant_web_redo(tmp_path, monkeypatch):
    from anthill.web.db import ChatMessage

    c, app_mod, conv_id = _solo_client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(ChatMessage(conversation_id=conv_id, role="assistant", content="an answer"))
    s.commit()
    body = c.get(f"/chat/{conv_id}").text
    # web is in play by default -> no per-answer "redo with web search"; the useful redos remain
    assert "Redo with web search" not in body
    assert "redoFromDom(this,'web')" not in body  # the web redo handler is gone too
    assert "Run the multi-step agent" not in body  # the manual agent re-run is retired (#421)
