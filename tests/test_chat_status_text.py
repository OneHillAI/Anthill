"""Chat status text that must match the real state (#159).

- The working line says "Thinking, locally." only when the model will think; otherwise "Writing, locally.".
- The "model too small" note belongs to web search, so it starts hidden while web search is off and follows the
  Web search toggle.
- The first message names a new conversation: the stream sends the title so the header stops saying
  "New conversation".

Model-free. The page's JavaScript is checked as template text; its behaviour needs a browser.
"""

import json
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod

TEMPLATE = Path(__file__).resolve().parents[1] / "anthill" / "web" / "templates" / "chat.html"


def _client(tmp_path, monkeypatch, *, title="New conversation", web_access_on=False):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(
        org_id=org.id, email="u@acme.com", role="member", active=True, web_access_on=web_access_on
    )
    session.add(user)
    session.flush()
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo", title=title)
    session.add(conversation)
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id


def _fallback_tag(html):
    m = re.search(r'<div id="chat-fallback-note"[^>]*>', html)
    return m.group(0) if m else None


def test_too_small_note_starts_hidden_while_web_search_is_off(tmp_path, monkeypatch):
    client, _app, cid = _client(tmp_path, monkeypatch)  # default model qwen2.5:3b cannot plan
    tag = _fallback_tag(client.get(f"/chat/{cid}").text)
    assert tag is not None
    assert " hidden" in tag


def test_too_small_note_shows_when_web_search_is_on(tmp_path, monkeypatch):
    client, _app, cid = _client(tmp_path, monkeypatch, web_access_on=True)
    tag = _fallback_tag(client.get(f"/chat/{cid}").text)
    assert tag is not None
    assert " hidden" not in tag


def test_too_small_note_follows_the_web_search_toggle():
    html = TEMPLATE.read_text(encoding="utf-8")
    sync = html[html.index("function syncTrustBanner()") :]
    sync = sync[: sync.index("\n      }\n")]
    assert "getElementById('chat-fallback-note')" in sync
    assert "fb.hidden = !_webBox.checked" in sync


def test_working_line_names_thinking_only_when_the_model_thinks():
    html = TEMPLATE.read_text(encoding="utf-8")
    helper = html[html.index("function workingText(think)") :]
    helper = helper[: helper.index("\n}\n")]
    assert "think !== false && !thinkUnavailable()" in helper
    assert "'Thinking, locally.'" in helper and "'Writing, locally.'" in helper
    # The label is chosen in one place; both sites that start a working row use it.
    assert "buildRowHTML(false, 'Thinking, locally.')" not in html
    assert "buildRowHTML(false, workingText(think))" in html
    assert "buildRowHTML(false, workingText(window._thinkOn))" in html


def test_header_can_be_retitled_by_the_stream_without_innerhtml():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert '<h1 id="chat-title">' in html
    handler = html[html.index("d.meta?.title") :]
    handler = handler[: handler.index("if (d.meta?.message_id)")]
    assert "getElementById('chat-title')" in handler
    assert "h.textContent = d.meta.title" in handler
    assert "innerHTML" not in handler


def _events(text):
    out = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[len("data: ") :]))
    return out


def _fake_answer(monkeypatch):
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["ok"]))


def test_stream_sends_the_title_when_the_first_message_names_the_chat(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    client, app_mod, cid = _client(tmp_path, monkeypatch)
    _fake_answer(monkeypatch)
    question = "What's a good hydration percentage for a beginner sourdough loaf? " * 2
    r = client.get(f"/chat/{cid}/stream", params={"message": question})
    assert r.status_code == 200
    events = _events(r.text)
    assert {"meta": {"title": question[:60]}} in events
    s = app_mod._SessionFactory()
    assert s.get(Conversation, cid).title == question[:60]


def test_stream_sends_no_title_for_an_already_named_chat(tmp_path, monkeypatch):
    client, _app, cid = _client(tmp_path, monkeypatch, title="Sourdough")
    _fake_answer(monkeypatch)
    r = client.get(f"/chat/{cid}/stream", params={"message": "and rye?"})
    assert not any("title" in (e.get("meta") or {}) for e in _events(r.text))


def test_stream_sends_no_title_when_confirming_a_proposal(tmp_path, monkeypatch):
    client, _app, cid = _client(tmp_path, monkeypatch)
    _fake_answer(monkeypatch)
    r = client.get(f"/chat/{cid}/stream", params={"message": "do it", "confirm": "true"})
    assert not any("title" in (e.get("meta") or {}) for e in _events(r.text))
