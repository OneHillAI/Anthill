"""Explicit "remember this: X" saves a durable memory IMMEDIATELY (issue #430).

Chat distillation is throttled to 6+ new messages, so a single "remember this: our deploys are on
Tuesdays" was silently dropped. A deterministic remember-intent now saves it on the spot (the
natural-language equivalent of the manual "+ Add memory" button) and acknowledges in the reply.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent import intent

# -- the detector: fires on real store-requests, never on reminiscing / questions ----------------

REMEMBERS = [
    "remember this: our production deploys happen only on Tuesdays",
    "Remember this our deploys are Tuesdays",
    "remember: the wifi password is hunter2",
    "please remember this: the office code is 4417",
    "note that the API rate limit is 100 per minute",
    "note to self: call the vendor back",
    "note: standups moved to 10am",
    "make a note that Sarah owns billing",
    "make a note to renew the domain",
    "keep in mind that prod is read-only on Fridays",
    "keep in mind our budget is fixed for Q3",
    "don't forget the client call is at 3pm",
    "don't forget that I prefer TypeScript",
    "for future reference, the staging URL is stg.acme.com",
    "for the record, I approved the vendor change",
    "jot this down: the retro is Thursday",
    "jot down that we use pnpm not npm",
]

NOT_REMEMBERS = [
    "do you remember what I asked earlier?",
    "I don't remember the answer",
    "remember when we tried Postgres?",
    "I'll always remember this trip",
    "can you remember to remind me later?",
    "note the difference between these two options",
    "what should I remember for the exam?",
    "nothing to remember here",
    "the note says the meeting is cancelled",
    "summarise this document for me",
    "remember",  # bare word, no fact
]


def test_looks_like_remember_precision():
    for m in REMEMBERS:
        assert intent.looks_like_remember(m) is True, m
    for m in NOT_REMEMBERS:
        assert intent.looks_like_remember(m) is False, m


def test_parse_remember_strips_leadin():
    assert (
        intent.parse_remember("remember this: deploys are on Tuesdays") == "deploys are on Tuesdays"
    )
    assert intent.parse_remember("note that the limit is 100") == "the limit is 100"
    assert intent.parse_remember("keep in mind that prod is read-only") == "prod is read-only"
    assert intent.parse_remember("for future reference, staging is x") == "staging is x"
    # nothing to strip -> whole message back (defensive)
    assert intent.parse_remember("just a plain sentence") == "just a plain sentence"


# -- the chat handler saves it immediately on a SHORT conversation (bypassing the throttle) -------


def _app(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    # the remember branch returns before inference, but the stream sets up a backend first
    monkeypatch.setattr("anthill.inference.base.build_backend", lambda cfg: object())
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="ada@acme.com", display_name="Ada", role="admin", active=True)
    s.add(u)
    s.flush()
    conv = Conversation(user_id=u.id, org_id=org.id, title="New conversation", plane="solo")
    s.add(conv)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return app_mod, client, {"org": org.id, "u": u.id, "conv": conv.id}


def test_remember_in_short_chat_saves_memory_immediately(tmp_path, monkeypatch):
    from anthill.web.db import ChatMessage, MemoryItem

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    fact = "our production deploys happen only on Tuesdays"
    r = client.get(f"/chat/{ids['conv']}/stream", params={"message": f"remember this: {fact}"})
    assert r.status_code == 200
    assert "memory" in r.text.lower()  # the acknowledgement was streamed

    s = app_mod._SessionFactory()
    mems = s.query(MemoryItem).filter(MemoryItem.user_id == ids["u"]).all()
    assert len(mems) == 1, "the single-exchange remember must be saved (not throttled)"
    assert mems[0].text == fact
    assert mems[0].scope == "personal" and mems[0].kind == "fact" and mems[0].source == "chat"
    # the conversation had only 2 messages (user + ack) - well under the 6-message distil threshold
    assert s.query(ChatMessage).filter(ChatMessage.conversation_id == ids["conv"]).count() == 2


def test_remember_duplicate_is_not_saved_twice(tmp_path, monkeypatch):
    from anthill.web.db import MemoryItem

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    msg = {"message": "note that we use pnpm not npm"}
    client.get(f"/chat/{ids['conv']}/stream", params=msg)
    r2 = client.get(f"/chat/{ids['conv']}/stream", params=msg)
    assert "already had that" in r2.text.lower()
    s = app_mod._SessionFactory()
    assert s.query(MemoryItem).filter(MemoryItem.user_id == ids["u"]).count() == 1
