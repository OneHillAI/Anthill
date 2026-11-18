"""The empty chat says what it runs on and offers a few ways to start (docs/specs/chat-empty-state.md).

Model-free: pages are rendered with a TestClient.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch, with_message=False):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import ChatMessage, Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    s.add(conv)
    s.flush()
    if with_message:
        s.add(ChatMessage(conversation_id=conv.id, role="user", content="hello"))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, conv.id


def test_an_empty_chat_shows_the_model_and_four_ways_to_start(tmp_path, monkeypatch):
    client, cid = _app(tmp_path, monkeypatch)
    page = client.get(f"/chat/{cid}").text
    assert 'id="chat-empty"' in page
    assert "<h2>Ask your own intelligence</h2>" in page
    assert (
        "qwen2.5:3b · runs on this computer" in page
    )  # the default local model, and where it runs
    assert page.count('class="chat-starter"') == 4
    assert 'href="/wiki"' in page  # adding a document is a link, the rest fill the box


def test_a_chat_with_messages_has_no_empty_state(tmp_path, monkeypatch):
    client, cid = _app(tmp_path, monkeypatch, with_message=True)
    page = client.get(f"/chat/{cid}").text
    assert 'id="chat-empty"' not in page
    assert 'class="msg user"' in page


def test_the_empty_state_hides_itself_once_a_message_is_on_the_page(tmp_path, monkeypatch):
    client, cid = _app(tmp_path, monkeypatch)
    page = client.get(f"/chat/{cid}").text
    # the script adds .msg rows to #messages; this rule is what removes the empty state without any script
    assert ".chat-messages:has(.msg) .chat-empty { display:none; }" in page
