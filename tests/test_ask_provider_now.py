"""/chat/{id}/ask-provider-now: the #1 UX follow-up to #820/#824. Waiting for a slow local model to
fully finish before ever offering the attached provider defeats the point of having a faster option -
chat.html now offers "Ask {Provider} instead" from the moment generation starts. Unlike
/chat/{id}/escalate-confirm (which appends to an ALREADY-SAVED local answer), there is no local answer
yet here - this creates its own independent assistant message for the conversation's current question.
Same consent/cap/audit contract as escalate-confirm; only the "when" and "what it attaches to" differ.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch, *, cap=20, used=0, consented=True, question="What is 2+2?"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import encrypt, make_token
    from anthill.web.db import ChatMessage, Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    session.add(
        OrgSettings(
            org_id=org.id,
            deployment_topology="solo",
            escalation_provider="groq",
            escalation_provider_key_enc=encrypt("gsk_secret"),
            escalation_mode="ask",
            escalation_cap_per_month=cap,
            escalations_this_month=used,
            escalations_reset_at=datetime.now(timezone.utc),
            escalation_consented=consented,
        )
    )
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.flush()
    # The question local is (still, in the real flow) generating an answer to - chat_stream saves this
    # BEFORE local generation starts, so it already exists by the time a user could click "ask instead".
    if question:
        session.add(ChatMessage(conversation_id=conversation.id, role="user", content=question))
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id


class _FakeAttachmentBackend:
    def __init__(self, reply="Provider answer."):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.reply


def test_ask_provider_now_creates_its_own_message_not_appending_to_anything(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=True)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["text"] == "Provider answer."
    assert body["provider_label"] == "Groq"
    assert len(fake_backend.calls) == 1
    assert fake_backend.calls[0][0].content == "What is 2+2?"  # asked local's own question

    session = app_mod._SessionFactory()
    assistant = session.query(db_mod.ChatMessage).filter_by(id=body["message_id"]).one()
    assert assistant.role == "assistant"
    assert assistant.content == "Provider answer."  # NOT appended to anything - it IS the answer
    assert assistant.escalated is True
    assert assistant.answered_locally is False
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalations_this_month == 1


def test_ask_provider_now_notifies_the_user_when_it_finishes(tmp_path, monkeypatch):
    """Found live: clicking "Ask {Provider} instead" then switching to a different conversation before
    it resolves left no way to learn it had finished - the answer was already saved either way, but
    nothing told the user it was there. Wires into the existing bell/push notify() chokepoint."""
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=True)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})
    assert resp.status_code == 200

    session = app_mod._SessionFactory()
    n = session.query(db_mod.Notification).one()
    assert "Groq" in n.title
    assert n.link == f"/chat/{conversation_id}"
    assert "Provider answer." in n.body


def test_ask_provider_now_remember_false_does_not_persist_consent(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    monkeypatch.setattr(
        app_mod, "_build_attachment_backend", lambda cfg, decrypt: _FakeAttachmentBackend()
    )

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "false"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalation_consented is False  # "just this once" - next turn is offered again


def test_ask_provider_now_respects_the_monthly_cap(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, cap=5, used=5)

    def _boom(*a, **k):
        raise AssertionError("must not build a backend once the cap is reached")

    monkeypatch.setattr(app_mod, "_build_attachment_backend", _boom)

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})

    assert resp.status_code == 400
    assert "limit" in resp.json()["error"]


def test_ask_provider_now_without_an_attachment_configured(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    cfg.escalation_provider = ""
    session.commit()

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})

    assert resp.status_code == 400
    assert "no inference provider" in resp.json()["error"]


def test_ask_provider_now_without_a_question_yet(tmp_path, monkeypatch):
    """An edge case (no user message saved yet) rather than the expected click-mid-generation path,
    but must still fail cleanly rather than 500."""
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, question=None)
    monkeypatch.setattr(
        app_mod, "_build_attachment_backend", lambda cfg, decrypt: _FakeAttachmentBackend()
    )

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})

    assert resp.status_code == 400
    assert "question" in resp.json()["error"]


# ── the chat page's JS globals chat.html's client-side offer logic reads ────────────────────────────


def test_chat_page_exposes_the_attached_provider_label_and_consent_state(tmp_path, monkeypatch):
    client, _app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=True)

    r = client.get(f"/chat/{conversation_id}")

    assert r.status_code == 200
    assert 'const ESCALATION_PROVIDER_LABEL = "Groq";' in r.text
    assert "let ESCALATION_CONSENTED = true;" in r.text


def test_chat_page_shows_no_provider_label_when_nothing_is_attached(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    cfg.escalation_provider = ""
    session.commit()

    r = client.get(f"/chat/{conversation_id}")

    assert r.status_code == 200
    assert 'const ESCALATION_PROVIDER_LABEL = "";' in r.text


def test_ask_provider_now_surfaces_a_provider_failure(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)

    class _FailingBackend:
        def chat(self, messages, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        app_mod, "_build_attachment_backend", lambda cfg, decrypt: _FailingBackend()
    )

    resp = client.post(f"/chat/{conversation_id}/ask-provider-now", data={"remember": "true"})

    assert resp.status_code == 502
    session = app_mod._SessionFactory()
    assert (
        session.query(db_mod.ChatMessage).filter_by(role="assistant").count() == 0
    )  # nothing saved
