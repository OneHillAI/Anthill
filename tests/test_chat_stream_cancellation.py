"""The known limitation documented in docs/specs/ask-provider-now.md ("local generation can't be
cleanly cancelled mid-request"), now closed: chat_stream's local-streaming loop checks
request.is_disconnected() once per token (iterate_in_threadpool pulls one token at a time, so a
disconnected client is simply never asked for another) and, on disconnect, skips the rest of the turn
entirely - no further meta events, no escalation decision, and critically no assistant message saved
for an answer nobody is listening for anymore (the point of "Ask {Provider} instead" abandoning this
stream: its answer, saved separately, IS this turn's answer instead).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch):
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
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    session.add(user)
    session.flush()
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id


def _disconnect_after(n):
    """A fake Request.is_disconnected: False for the first `n` calls, True from then on - simulates
    the client walking away partway through local generation (e.g. clicking "Ask {Provider} instead")."""
    calls = {"n": 0}

    async def _fake(self):
        calls["n"] += 1
        return calls["n"] > n

    return _fake, calls


def test_client_disconnect_stops_local_generation_and_saves_nothing(tmp_path, monkeypatch):
    from starlette.requests import Request

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "anthill.wiki.ask.ask_stream",
        lambda *a, **k: iter(["one ", "two ", "three ", "four ", "five "]),
    )
    fake_is_disconnected, calls = _disconnect_after(2)  # "receive" 2 tokens, then walk away
    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    # Stopped well short of all 5 tokens - proves generation was actually cut short, not just that
    # the response happens to look normal.
    assert "three" not in response.text
    assert "four" not in response.text
    assert "five" not in response.text
    assert '"cache_hit"' not in response.text  # none of the post-loop meta events fired either
    assert '"answered_locally"' not in response.text
    assert (
        calls["n"] == 3
    )  # checked once per token actually consumed (2 tokens + the disconnect check
    # that stopped the 3rd) - never asked ask_stream for a 4th or 5th token at all

    session = app_mod._SessionFactory()
    assert (
        session.query(db_mod.ChatMessage).filter_by(role="assistant").count() == 0
    )  # nothing saved


def test_normal_completion_is_unaffected_when_the_client_never_disconnects(tmp_path, monkeypatch):
    """Sanity check: the new per-token check doesn't change behaviour for the ordinary case."""
    from starlette.requests import Request

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak ", "answer."]))

    async def _never_disconnected(self):
        return False

    monkeypatch.setattr(Request, "is_disconnected", _never_disconnected)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak " in response.text and "answer." in response.text
    assert '"answered_locally"' in response.text

    session = app_mod._SessionFactory()
    assistant = session.query(db_mod.ChatMessage).filter_by(role="assistant").one()
    assert assistant.content == "Weak answer."
