"""Automated mode's escalation, wired into chat_stream (compound-compute-tiers spec): fires AFTER the
lead's own answer has already streamed, never blocking the first answer. The trigger decision itself
(should_escalate_automated/grade_answer_locally) is covered by tests/test_escalation_trigger.py; these
tests prove the SSE wiring - the escalating/escalated meta events, the appended answer text, the
persisted assistant message, and the monthly-cap bookkeeping.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch, *, escalation_mode="automated", cap=20, used=0, consented=True):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import encrypt, make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

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
    session.add(
        OrgSettings(
            org_id=org.id,
            deployment_topology="solo",
            local_serve_url="http://127.0.0.1:11435/v1",
            local_serve_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
            escalation_provider="groq",
            escalation_provider_key_enc=encrypt("gsk_secret"),
            escalation_mode=escalation_mode,
            escalation_cap_per_month=cap,
            escalations_this_month=used,
            # A null reset timestamp reads as "due" (escalation.reset_monthly_escalation_count_if_due)
            # and would silently wipe `used` back to 0 on the very first cap check - set it to "now"
            # so a fixture-supplied `used` actually sticks for cap-reached assertions.
            escalations_reset_at=datetime.now(timezone.utc),
            escalation_consented=consented,
        )
    )
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id


class _FakeAttachmentBackend:
    def __init__(self, reply="Escalated answer."):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.reply


def test_automated_escalation_appends_the_stronger_answer_and_marks_the_turn(tmp_path, monkeypatch):
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak ", "answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak " in response.text and "answer." in response.text  # streamed as separate tokens
    assert "Escalated answer." in response.text
    assert '"escalating": true' in response.text
    assert '"escalated": true' in response.text
    assert len(fake_backend.calls) == 1  # exactly one paid escalation call

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == "Weak answer.\n\nEscalated answer."
    assert assistant.escalated is True  # persisted, so chat.html's badge survives a page reload
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalations_this_month == 1  # cap bookkeeping advanced


def test_automated_mode_does_not_escalate_a_confident_answer(tmp_path, monkeypatch):
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Confident answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: False)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Confident answer." in response.text
    assert '"escalating"' not in response.text
    assert '"escalated"' not in response.text
    assert fake_backend.calls == []  # never spent the paid call

    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalations_this_month == 0
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.escalated is False


def test_automated_mode_off_never_calls_the_trigger(tmp_path, monkeypatch):
    import anthill.web.escalation as esc_mod

    client, _app_mod, conversation_id = _client(tmp_path, monkeypatch, escalation_mode="ask")

    def _boom(*a, **k):
        raise AssertionError("should_escalate_automated must not run in ask mode")

    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", _boom)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak answer." in response.text


def test_automated_escalation_skips_silently_once_the_monthly_cap_is_reached(tmp_path, monkeypatch):
    import anthill.web.escalation as esc_mod

    client, _app_mod, conversation_id = _client(tmp_path, monkeypatch, cap=5, used=5)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))

    def _boom(*a, **k):
        raise AssertionError("the trigger must not even run once the cap is reached")

    monkeypatch.setattr(esc_mod, "should_escalate_automated", _boom)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak answer." in response.text
    assert '"escalating"' not in response.text


def test_automated_escalation_offers_consent_before_first_use(tmp_path, monkeypatch):
    """Privacy gate: Automated mode must never call a third-party provider without an explicit human
    click the first time - regardless of the account's mode setting. Until then it offers instead."""
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak ", "answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak " in response.text and "answer." in response.text  # the lead's answer still shows
    assert fake_backend.calls == []  # nothing left the device without a click
    assert '"escalating": true' not in response.text
    assert '"escalated": true' not in response.text
    assert '"escalation_offer"' in response.text
    assert '"provider": "groq"' in response.text
    assert '"provider_label": "Groq"' in response.text

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == "Weak answer."  # nothing appended yet
    assert assistant.escalated is False
    assert f'"message_id": {assistant.id}' in response.text
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalations_this_month == 0  # the cap wasn't spent on an unapproved call


# ── #820 follow-up: an always-available manual offer, independent of the self-grader's verdict ──────


def test_ask_mode_still_gets_a_manual_escalation_offer(tmp_path, monkeypatch):
    """#820: Ask mode never runs the automated trigger at all (test_automated_mode_off_never_calls_
    the_trigger above), but the human should still get a one-tap way to check - under-escalation is
    fixed by always offering the choice, not by better auto-detection."""
    client, _app_mod, conversation_id = _client(
        tmp_path, monkeypatch, escalation_mode="ask", consented=True
    )
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert '"escalation_offer"' in response.text
    assert (
        '"consented": true' in response.text
    )  # already consented -> the one-tap shape, not 3 buttons


def test_a_confident_automated_answer_still_gets_a_manual_offer(tmp_path, monkeypatch):
    """#820: the whole point - a small local model can be confidently WRONG, so "the grader was
    confident" must not be the last word. The human still gets to check."""
    import anthill.web.escalation as esc_mod

    client, _app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=True)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Confident answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: False)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert '"escalation_offer"' in response.text
    assert '"consented": true' in response.text


def test_manual_offer_is_the_full_consent_card_when_not_yet_consented(tmp_path, monkeypatch):
    """Ask mode, never consented: the offer must be the existing 3-button first-use consent card
    (consented: false), not the one-tap shape - a not-yet-trusted attachment still needs approval."""
    client, _app_mod, conversation_id = _client(
        tmp_path, monkeypatch, escalation_mode="ask", consented=False
    )
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert '"escalation_offer"' in response.text
    assert '"consented": false' in response.text


def test_no_manual_offer_once_the_monthly_cap_is_reached(tmp_path, monkeypatch):
    """The offer must not tease a click that /chat/{id}/escalate-confirm would just reject."""
    client, _app_mod, conversation_id = _client(
        tmp_path, monkeypatch, escalation_mode="ask", cap=5, used=5
    )
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert '"escalation_offer"' not in response.text


def test_no_duplicate_offer_when_automated_mode_already_offered(tmp_path, monkeypatch):
    """The pre-existing not-yet-consented Automated-mode offer and the new always-offer must not both
    fire on the same turn - exactly one escalation_offer event per answer."""
    import anthill.web.escalation as esc_mod

    client, _app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert response.text.count('"escalation_offer"') == 1


def test_no_manual_offer_without_an_attachment_configured(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    cfg.escalation_provider = ""
    session.commit()
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert '"escalation_offer"' not in response.text


def test_automated_escalation_failure_never_fails_the_already_shown_answer(tmp_path, monkeypatch):
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)

    def _raise(cfg, decrypt):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(app_mod, "_build_attachment_backend", _raise)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak answer." in response.text
    assert '"error"' not in response.text  # the local answer's own success is untouched

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == "Weak answer."
    assert assistant.generation_failed is False


class _FailingAttachmentBackend:
    """Simulates the provider itself rejecting the call - e.g. a curated escalation_model the
    provider has since dropped (#820) - distinct from _build_attachment_backend failing to construct
    a backend at all (covered above)."""

    def chat(self, messages, **kw):
        raise RuntimeError("The model endpoint returned 404: resource not found")


def test_automated_escalation_provider_failure_is_surfaced_not_silent(tmp_path, monkeypatch):
    """#820: the provider call itself failing (a stale curated model, a transient 5xx, ...) was
    swallowed by a bare except/pass - the client never learned the attempt happened at all, so
    "Checking with {Provider}..." just vanished with the local answer already shown. It must now emit
    a distinct meta event the client can react to, while still never touching the already-successful
    local answer."""
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)
    monkeypatch.setattr(
        app_mod, "_build_attachment_backend", lambda cfg, decrypt: _FailingAttachmentBackend()
    )

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak answer." in response.text  # the lead's own answer is untouched
    assert '"escalation_failed": true' in response.text
    assert '"escalated": true' not in response.text  # never fires alongside a failure

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == "Weak answer."
    assert assistant.escalated is False
    assert assistant.generation_failed is False
    cfg = session.query(db_mod.OrgSettings).first()
    # record_escalation_used() already ran (before the failing call) - a failed attempt still counts
    # against the cap, matching the manual /escalate-confirm path's behaviour.
    assert cfg.escalations_this_month == 1


def test_a_failure_before_the_provider_call_still_surfaces_escalation_failed(tmp_path, monkeypatch):
    """Found live: a failure anywhere between the "escalating" signal and the provider call itself
    (record_escalation_used, db.commit, audit.log_inference_call - not just the call below) used to
    fall straight through to the outer bare except/pass, so the client never got escalated OR
    escalation_failed - "Checking with {Provider}..." was left stuck forever with nothing to react to.
    """
    import anthill.web.escalation as esc_mod

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)
    monkeypatch.setattr(
        app_mod, "_build_attachment_backend", lambda cfg, decrypt: _FakeAttachmentBackend()
    )

    def _boom(cfg):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(esc_mod, "record_escalation_used", _boom)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Weak answer." in response.text  # the lead's own answer is untouched
    assert '"escalating": true' in response.text  # the client WAS told an attempt started
    assert '"escalation_failed": true' in response.text  # ... and that it did not silently vanish
    assert '"escalated": true' not in response.text


# ── /chat/{conv_id}/escalate-confirm: the human click behind the offer above ────────────────────────


def _offered_message(client, app_mod, conversation_id, monkeypatch):
    """Drive a real turn to the offer state (mirrors the consent test above) and return its message id."""
    import anthill.web.escalation as esc_mod

    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Weak answer."]))
    monkeypatch.setattr(esc_mod, "should_escalate_automated", lambda *a, **k: True)
    client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})
    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    return assistant.id


def test_escalate_confirm_remember_true_fires_and_persists_consent(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["text"] == "Escalated answer."
    assert len(fake_backend.calls) == 1

    session = app_mod._SessionFactory()
    assistant = session.query(db_mod.ChatMessage).filter_by(id=message_id).one()
    assert assistant.content == "Weak answer.\n\nEscalated answer."
    assert assistant.escalated is True
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalation_consented is True  # future turns won't be offered again
    assert cfg.escalations_this_month == 1


def test_escalate_confirm_notifies_the_user_when_it_finishes(tmp_path, monkeypatch):
    """Found live: a user who switched to a different conversation while this was in flight had no way
    to learn it had finished - the answer was already saved either way, but nothing told them it was
    there. Wires into the existing bell/push notify() chokepoint instead of a new alert path."""
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "true"},
    )
    assert resp.status_code == 200

    session = app_mod._SessionFactory()
    n = session.query(db_mod.Notification).one()
    assert "Groq" in n.title
    assert n.link == f"/chat/{conversation_id}"
    assert "Escalated answer." in n.body


def test_escalate_confirm_remember_false_fires_without_persisting_consent(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "false"},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(fake_backend.calls) == 1  # this one request still goes through

    session = app_mod._SessionFactory()
    cfg = session.query(db_mod.OrgSettings).first()
    assert cfg.escalation_consented is False  # but the next turn is offered again
    assistant = session.query(db_mod.ChatMessage).filter_by(id=message_id).one()
    assert assistant.escalated is True


def test_escalate_confirm_rejects_an_already_escalated_message(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)
    client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "false"},
    )

    resp = client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "true"},
    )

    assert resp.status_code == 400
    assert len(fake_backend.calls) == 1  # the second attempt never reached the provider


def test_escalate_confirm_respects_the_monthly_cap(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(
        tmp_path, monkeypatch, consented=False, cap=1, used=1
    )
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)
    fake_backend = _FakeAttachmentBackend()
    monkeypatch.setattr(app_mod, "_build_attachment_backend", lambda cfg, decrypt: fake_backend)

    resp = client.post(
        f"/chat/{conversation_id}/escalate-confirm",
        data={"message_id": message_id, "remember": "true"},
    )

    assert resp.status_code == 400
    assert fake_backend.calls == []


def test_escalate_confirm_rejects_a_message_from_another_conversation(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _client(tmp_path, monkeypatch, consented=False)
    message_id = _offered_message(client, app_mod, conversation_id, monkeypatch)

    resp = client.post(
        "/chat/999999/escalate-confirm",
        data={"message_id": message_id, "remember": "true"},
    )

    assert resp.status_code == 404


# ── the always-offer check must also fire after agent mode, not just plain chat ─────────────────────


class _FakeAgentExecutor:
    """A minimal stand-in for AgentExecutor: no tool calls, straight to a final answer."""

    def __init__(self, backend, tools, **kw):
        pass

    def stream(self, goal, *, context=""):
        yield "Agent's final answer."


def test_agent_mode_answer_still_gets_a_manual_escalation_offer(tmp_path, monkeypatch):
    """Found live: a question the router silently routed into agent mode (agent_auto, #421) - or one
    the user explicitly ran with Agent - never offered the one-tap "check with your provider" choice,
    because that check lived entirely inside the plain-chat branch. The founder's own stated intent
    ("always offer... independent of escalation_mode") applies here exactly as much as plain chat."""
    import anthill.agent.executor as executor_mod

    client, _app_mod, conversation_id = _client(
        tmp_path, monkeypatch, escalation_mode="ask", consented=True
    )
    monkeypatch.setattr(executor_mod, "AgentExecutor", _FakeAgentExecutor)

    response = client.get(
        f"/chat/{conversation_id}/stream",
        params={"message": "plan my week", "agent_mode": "true"},
    )

    assert response.status_code == 200
    assert '"escalation_offer"' in response.text


def test_research_mode_answer_still_gets_a_manual_escalation_offer(tmp_path, monkeypatch):
    client, _app_mod, conversation_id = _client(
        tmp_path, monkeypatch, escalation_mode="ask", consented=True
    )
    monkeypatch.setattr(
        "anthill.research.research_stream", lambda *a, **k: iter(["A researched answer."])
    )

    response = client.get(
        f"/chat/{conversation_id}/stream",
        params={"message": "deep dive on this", "research": "true", "confirm": "true"},
    )

    assert response.status_code == 200
    assert '"escalation_offer"' in response.text
