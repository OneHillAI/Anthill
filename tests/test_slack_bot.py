"""Inbound Slack bot: your team asks Anthill from Slack and gets an org-wiki answer in-thread.

Covers the auth boundary (Slack request signature), the URL-verification handshake, membership mapping
(only org members get answers), the app_mention + slash-command flows, and the settings save. The
answer engine (ask / plane routing / context) is stubbed - this exercises the Slack wiring, model-free.
"""

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web import ingest_push
from anthill.web import slack_bot as sb
from anthill.web.crypto import encrypt, make_token
from anthill.web.db import Organization, SlackBot, User

SECRET = "test-signing-secret"


class _FakePlane:
    backend = "openai"
    base_url = "http://model.local/v1"
    model = "test-model"
    api_key = "k"
    use_personal_context = False
    ephemeral = False


def _app(tmp_path, monkeypatch, *, member_email="ada@acme.com", enabled=True):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    # run the background worker inline instead of a daemon thread (single seam, no global patch)
    monkeypatch.setattr(app_mod, "_spawn", lambda target, *a, **k: target(*a, **k))
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email=member_email, display_name="Ada", role="admin", active=True)
    s.add(u)
    s.add(
        SlackBot(
            org_id=org.id,
            team_id="T1",
            bot_user_id="UBOT",
            bot_token_enc=encrypt("xoxb-test"),
            signing_secret_enc=encrypt(SECRET),
            enabled=enabled,
        )
    )
    s.commit()
    client = TestClient(app_mod.app)
    return app_mod, client, {"org": org.id, "u": u.id}


def _stub_engine(monkeypatch, answer="Deploys are on Tuesdays."):
    """Stub the answer path so only the Slack wiring is under test."""
    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", lambda *a, **k: _FakePlane())
    monkeypatch.setattr("anthill.web.agent_context.agent_context_for", lambda *a, **k: ("", []))
    monkeypatch.setattr("anthill.wiki.ask.ask", lambda ws, q, backend, **k: (answer, [], False))


def _post_event(client, body, *, secret=SECRET, ts=None):
    raw = json.dumps(body).encode()
    ts = ts or str(int(time.time()))
    sig = ingest_push.slack_signature(secret, ts, raw)
    return client.post(
        "/slack/events",
        content=raw,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/json",
        },
    )


def test_url_verification_returns_challenge(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    r = client.post("/slack/events", json={"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json().get("challenge") == "abc123"


def test_bad_signature_is_401(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    r = _post_event(
        client,
        {"event": {"type": "app_mention", "user": "U1", "text": "<@UBOT> hi", "channel": "C1"}},
        secret="wrong-secret",
    )
    assert r.status_code == 401


def test_app_mention_from_member_is_answered_in_thread(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    _stub_engine(monkeypatch)
    posted = []
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "ada@acme.com")
    monkeypatch.setattr(
        sb, "post_message", lambda tok, ch, text, thread_ts="": posted.append((ch, text, thread_ts))
    )
    r = _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT> when do we deploy?",
                "channel": "C1",
                "ts": "111.1",
            }
        },
    )
    assert r.status_code == 200
    assert posted, "the bot must post an answer"
    ch, text, thread_ts = posted[0]
    assert ch == "C1" and "Tuesdays" in text and thread_ts == "111.1"


def test_non_member_gets_polite_refusal(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    _stub_engine(monkeypatch)
    posted = []
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "stranger@other.com")
    monkeypatch.setattr(sb, "post_message", lambda tok, ch, text, thread_ts="": posted.append(text))
    r = _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "U9",
                "text": "<@UBOT> secrets?",
                "channel": "C1",
            }
        },
    )
    assert r.status_code == 200
    assert posted and "invite" in posted[0].lower() and "stranger@other.com" in posted[0]


def test_bot_own_message_is_ignored(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    r = _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "UBOT",
                "text": "<@UBOT> loop?",
                "channel": "C1",
            }
        },
    )
    assert r.status_code == 200 and r.json().get("ignored") is True


def test_slash_command_posts_via_response_url(tmp_path, monkeypatch):
    _, client, _ = _app(tmp_path, monkeypatch)
    _stub_engine(monkeypatch, answer="Our refund window is 30 days.")
    said = []
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "ada@acme.com")
    monkeypatch.setattr(sb, "respond_url", lambda url, text, **k: said.append((url, text)))
    from urllib.parse import urlencode

    body = urlencode(
        {
            "user_id": "U1",
            "channel_id": "C1",
            "text": "what is our refund policy?",
            "response_url": "https://hooks.slack.test/r",
        }
    ).encode()
    ts = str(int(time.time()))
    sig = ingest_push.slack_signature(SECRET, ts, body)
    r = client.post(
        "/slack/command",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 200 and "moment" in r.json()["text"].lower()  # immediate ack
    assert said and said[0][0] == "https://hooks.slack.test/r" and "refund" in said[0][1].lower()


def test_settings_save_rejects_bad_token_and_saves_good(tmp_path, monkeypatch):
    app_mod_, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    client.cookies.set("session_token", make_token(ids["u"], ids["org"], "admin"))

    # a token Slack rejects -> error, bot stays disabled
    monkeypatch.setattr(sb, "auth_test", lambda tok: {"ok": False, "error": "invalid_auth"})
    r = client.post(
        "/settings/slack",
        data={"bot_token": "xoxb-bad", "enabled": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=bad_token" in r.headers["location"]

    # a good token -> stored, bot user learned, enabled once both secrets present
    monkeypatch.setattr(
        sb, "auth_test", lambda tok: {"ok": True, "user_id": "UNEW", "team_id": "TNEW"}
    )
    r = client.post(
        "/settings/slack",
        data={"bot_token": "xoxb-good", "signing_secret": "s3cret", "enabled": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    s = app_mod_._SessionFactory()
    bot = s.query(SlackBot).filter(SlackBot.org_id == ids["org"]).first()
    assert bot.enabled is True and bot.bot_user_id == "UNEW" and bot.team_id == "TNEW"


# -- one-click OAuth install ("Add to Slack") -----------------------------------------------------


def _admin(client, ids):
    client.cookies.set("session_token", make_token(ids["u"], ids["org"], "admin"))


def _set_client(app_mod_, org_id, *, secret=True):
    s = app_mod_._SessionFactory()
    bot = s.query(SlackBot).filter(SlackBot.org_id == org_id).first()
    bot.client_id = "12345.67890"
    if secret:
        bot.client_secret_enc = encrypt("cs3cret")
    s.commit()


def test_authorize_url_has_client_scopes_state():
    url = sb.authorize_url("CID", "https://x/slack/oauth/callback", "STATE")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=CID" in url and "state=STATE" in url
    assert "chat%3Awrite" in url and "users%3Aread.email" in url  # scopes urlencoded


def test_oauth_start_redirects_to_slack(tmp_path, monkeypatch):
    app_mod_, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    _set_client(app_mod_, ids["org"])
    _admin(client, ids)
    r = client.get("/slack/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert (
        loc.startswith("https://slack.com/oauth/v2/authorize?") and "client_id=12345.67890" in loc
    )


def test_oauth_start_without_client_id_errors(tmp_path, monkeypatch):
    _, client, ids = _app(tmp_path, monkeypatch, enabled=False)  # no client_id set
    _admin(client, ids)
    r = client.get("/slack/oauth/start", follow_redirects=False)
    assert r.status_code == 302 and "error=need_client" in r.headers["location"]


def test_oauth_callback_installs_and_enables(tmp_path, monkeypatch):
    app_mod_, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    _set_client(app_mod_, ids["org"])
    _admin(client, ids)
    monkeypatch.setattr(
        sb,
        "oauth_access",
        lambda cid, cs, code, ru: {
            "ok": True,
            "access_token": "xoxb-installed",
            "bot_user_id": "UNEW",
            "team": {"id": "TNEW"},
        },
    )
    state = app_mod_._slack_oauth_serializer().dumps({"org": ids["org"]})
    r = client.get(f"/slack/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    s = app_mod_._SessionFactory()
    bot = s.query(SlackBot).filter(SlackBot.org_id == ids["org"]).first()
    from anthill.web.crypto import decrypt

    assert decrypt(bot.bot_token_enc) == "xoxb-installed"
    assert bot.bot_user_id == "UNEW" and bot.team_id == "TNEW" and bot.enabled is True


def test_oauth_callback_rejects_bad_state(tmp_path, monkeypatch):
    app_mod_, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    _set_client(app_mod_, ids["org"])
    _admin(client, ids)
    r = client.get("/slack/oauth/callback?code=abc&state=tampered", follow_redirects=False)
    assert r.status_code == 302 and "error=state" in r.headers["location"]


def test_oauth_callback_handles_user_denial(tmp_path, monkeypatch):
    _, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    _admin(client, ids)
    r = client.get("/slack/oauth/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 302 and "error=denied" in r.headers["location"]


def test_settings_save_stores_client_credentials(tmp_path, monkeypatch):
    app_mod_, client, ids = _app(tmp_path, monkeypatch, enabled=False)
    _admin(client, ids)
    r = client.post(
        "/settings/slack",
        data={"client_id": "99.88", "client_secret": "shh", "signing_secret": "sign"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    s = app_mod_._SessionFactory()
    bot = s.query(SlackBot).filter(SlackBot.org_id == ids["org"]).first()
    from anthill.web.crypto import decrypt

    assert bot.client_id == "99.88" and decrypt(bot.client_secret_enc) == "shh"


def test_ensure_columns_upgrades_preexisting_slack_bots_table(tmp_path):
    """An install that created slack_bots before the OAuth columns existed gets them added (there is
    no migration framework, so create_tables tops up missing columns)."""
    from sqlalchemy import create_engine, text

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:  # the pre-OAuth shape (no client_id / client_secret_enc)
        c.execute(
            text(
                "CREATE TABLE slack_bots (id INTEGER PRIMARY KEY, org_id INTEGER, "
                "signing_secret_enc TEXT)"
            )
        )
    db_mod.create_tables(eng)
    with eng.begin() as c:
        cols = {row[1] for row in c.execute(text("PRAGMA table_info(slack_bots)")).fetchall()}
    assert "client_id" in cols and "client_secret_enc" in cols


# -- threaded follow-up context ------------------------------------------------------------------


def _capture_ask(monkeypatch):
    """Stub the answer engine and record the history passed on each call."""
    calls = []

    def fake_ask(ws, q, backend, **k):
        calls.append({"q": q, "history": list(k.get("history") or [])})
        return (f"answer to: {q}", [], False)

    monkeypatch.setattr("anthill.wiki.ask.ask", fake_ask)
    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", lambda *a, **k: _FakePlane())
    monkeypatch.setattr("anthill.web.agent_context.agent_context_for", lambda *a, **k: ("", []))
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "ada@acme.com")
    return calls


def _mention(client, text, *, channel="C1", ts="1.1", thread_ts=None):
    ev = {
        "type": "app_mention",
        "user": "U1",
        "text": f"<@UBOT> {text}",
        "channel": channel,
        "ts": ts,
    }
    if thread_ts:
        ev["thread_ts"] = thread_ts
    return _post_event(client, {"event": ev})


def test_thread_followup_reuses_conversation_and_carries_history(tmp_path, monkeypatch):
    from anthill.web.db import ChatMessage, Conversation

    app_mod_, client, _ = _app(tmp_path, monkeypatch)
    calls = _capture_ask(monkeypatch)
    monkeypatch.setattr(sb, "post_message", lambda *a, **k: None)

    _mention(client, "q1", ts="111.1")  # starts the thread
    _mention(client, "q2", ts="111.9", thread_ts="111.1")  # a reply in the same thread

    s = app_mod_._SessionFactory()
    convs = s.query(Conversation).filter(Conversation.slack_thread == "C1:111.1").all()
    assert len(convs) == 1  # one conversation for the whole thread
    assert s.query(ChatMessage).filter(ChatMessage.conversation_id == convs[0].id).count() == 4
    # the first turn had no history; the second turn saw the first Q + A
    assert calls[0]["history"] == []
    assert ("user", "q1") in calls[1]["history"] and ("assistant", "answer to: q1") in calls[1][
        "history"
    ]


def test_separate_threads_are_isolated(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    app_mod_, client, _ = _app(tmp_path, monkeypatch)
    calls = _capture_ask(monkeypatch)
    monkeypatch.setattr(sb, "post_message", lambda *a, **k: None)

    _mention(client, "in thread A", ts="111.1")
    _mention(client, "in thread B", ts="222.2")  # a different root ts = a different thread

    s = app_mod_._SessionFactory()
    assert s.query(Conversation).filter(Conversation.slack_thread.like("C1:%")).count() == 2
    assert calls[1]["history"] == []  # thread B does not see thread A's turn


def test_dm_is_one_ongoing_conversation_unthreaded(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    app_mod_, client, _ = _app(tmp_path, monkeypatch)
    calls = _capture_ask(monkeypatch)
    posted = []
    monkeypatch.setattr(
        sb, "post_message", lambda tok, ch, text, thread_ts="": posted.append(thread_ts)
    )

    def _dm(text, ts):
        ev = {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": text,
            "channel": "D1",
            "ts": ts,
        }
        return _post_event(client, {"event": ev})

    _dm("first dm", "a1")
    _dm("second dm", "a2")

    s = app_mod_._SessionFactory()
    convs = s.query(Conversation).filter(Conversation.slack_thread == "dm:D1").all()
    assert len(convs) == 1  # the whole DM is one conversation
    assert ("user", "first dm") in calls[1]["history"]  # continuity across DMs
    assert posted == ["", ""]  # DMs replied un-threaded


def test_member_idea_is_routed_to_intake_not_answered(tmp_path, monkeypatch):
    from anthill.contribute.interaction import DISCLOSURE
    from anthill.web.db import ContributionProposal

    app_mod, client, _ids = _app(tmp_path, monkeypatch)
    _stub_engine(monkeypatch)  # the answer path would say "Tuesdays"; an idea must NOT be answered
    # a complete spec so intake reports it ready (the definition-of-ready path)
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: None)
    monkeypatch.setattr(
        "anthill.contribute.distil_proposal",
        lambda idea, ref, backend: {
            "title": "Export the org wiki to PDF",
            "kind": "feature",
            "spec": "## Problem\n...\n## Acceptance criteria\n- a PDF is produced",
            "priority": "medium",
            "complete": True,
            "needs_detail": "",
        },
    )
    posted = []
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "ada@acme.com")
    monkeypatch.setattr(sb, "post_message", lambda tok, ch, text, thread_ts="": posted.append(text))
    r = _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT> feature request: export the org wiki to PDF on a schedule",
                "channel": "C1",
                "ts": "222.2",
            }
        },
    )
    assert r.status_code == 200
    assert posted, "the bot must reply"
    reply = posted[0]
    assert reply.startswith(DISCLOSURE)  # discloses it is automated
    assert "intake" in reply.lower() and "Export the org wiki to PDF" in reply
    assert "Tuesdays" not in reply  # routed to intake, NOT answered
    s = app_mod._SessionFactory()
    props = s.query(ContributionProposal).filter(ContributionProposal.source == "slack").all()
    s.close()
    assert len(props) == 1 and props[0].completeness == "complete"


def test_member_answer_carries_disclosure(tmp_path, monkeypatch):
    from anthill.contribute.interaction import DISCLOSURE

    _, client, _ = _app(tmp_path, monkeypatch)
    _stub_engine(monkeypatch, answer="We deploy on Tuesdays.")
    posted = []
    monkeypatch.setattr(sb, "user_email", lambda tok, uid: "ada@acme.com")
    monkeypatch.setattr(sb, "post_message", lambda tok, ch, text, thread_ts="": posted.append(text))
    _post_event(
        client,
        {
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT> when do we deploy?",
                "channel": "C1",
                "ts": "333.3",
            }
        },
    )
    assert posted and posted[0].startswith(DISCLOSURE) and "Tuesdays" in posted[0]
