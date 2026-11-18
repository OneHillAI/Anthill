"""The chat box's two on/off buttons look like switches, and an answer says where it ran
(docs/specs/chat-switches-and-answer-source.md). Model-free: pages are rendered with a TestClient."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch, *, answered_locally=True):
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
    s.add(
        ChatMessage(
            conversation_id=conv.id,
            role="assistant",
            content="an answer",
            model="some-model:7b",
            answered_locally=answered_locally,
        )
    )
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, conv.id


def test_the_two_on_off_buttons_are_drawn_as_switches_with_the_state_in_words(
    tmp_path, monkeypatch
):
    client, cid = _client(tmp_path, monkeypatch)
    page = client.get(f"/chat/{cid}").text
    assert ".opt-toggle[aria-pressed]::before" in page  # the switch track
    assert (
        '.opt-toggle[aria-pressed="true"]::after { left:22px; }' in page
    )  # the knob moves when on
    assert (
        '<span id="opt-web-label">Web search off</span>' in page
    )  # and the state is still in words
    assert '<span id="opt-think-label">Thinking off</span>' in page
    # the knowledge picker is not an on/off control, so it has no pressed state and no switch
    scope = page[page.index('class="opt-toggle opt-scope-btn"') :].split(">", 1)[0]
    assert "aria-pressed" not in scope


def test_an_answer_that_ran_here_says_so_in_words(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch, answered_locally=True)
    page = client.get(f"/chat/{cid}").text
    assert (
        '<span class="src-chip local" title="Answered on this device - nothing left it">On this device</span>'
        in page
    )
    assert "Sent over the network" not in page.split('id="bubble-')[1].split("thumbs")[0]
    assert "\U0001f4bb" not in page  # no laptop emoji standing in for the words


def test_an_answer_that_left_the_device_says_so_and_names_the_model_on_hover(tmp_path, monkeypatch):
    client, cid = _client(tmp_path, monkeypatch, answered_locally=False)
    page = client.get(f"/chat/{cid}").text
    assert (
        '<span class="src-chip" title="Answered by some-model:7b over the network">Sent over the network</span>'
        in page
    )
    assert "On this device</span>" not in page.split('id="bubble-')[1].split("thumbs")[0]
    assert "☁" not in page.split('id="bubble-')[1].split("thumbs")[0]  # no cloud emoji either
