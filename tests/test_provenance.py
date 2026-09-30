"""Content provenance: every chat turn carries a content-addressable hash, and a wiki write proposed from
that content carries a verifiable fingerprint + a link back to the chat it came from."""

import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def test_chat_message_gets_a_provenance_hash_on_insert(tmp_path):
    from anthill.web.db import ChatMessage, Conversation, Organization, User, message_provenance

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@a.com")
    s.add(u)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=u.id)
    s.add(conv)
    s.flush()

    m = ChatMessage(conversation_id=conv.id, role="user", content="hello world")
    s.add(m)
    s.commit()
    # stamped automatically, deterministic sha256 over (conversation, role, content)
    assert m.provenance == message_provenance(conv.id, "user", "hello world")
    assert len(m.provenance) == 64

    # content-addressable: the same input yields the same hash, a different input a different one
    m2 = ChatMessage(conversation_id=conv.id, role="user", content="hello world")
    m3 = ChatMessage(conversation_id=conv.id, role="user", content="something else")
    s.add_all([m2, m3])
    s.commit()
    assert m2.provenance == m.provenance
    assert m3.provenance != m.provenance


def test_propose_wiki_write_stamps_provenance_on_a_queued_review(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    import anthill.wiki.review as review_mod
    from anthill.web.db import Organization, OrgSettings, WikiReview

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    from fk_seed import seed_org_and_users

    seed_org_and_users(s, org_id=org.id)  # a user for the proposed_by on the queued review
    # the review cites source_conversation_id=42 / source_message_id=7 below, which must exist under
    # strict foreign keys (the provenance link is real).
    from anthill.web.db import ChatMessage, Conversation

    s.add(Conversation(id=42, org_id=org.id, user_id=1, title="src"))
    s.flush()
    s.add(
        ChatMessage(
            id=7,
            conversation_id=42,
            role="user",
            content="q",
            model="m",
            cache_hit=False,
            wiki_slugs="",
            provenance="",
        )
    )
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    org_id = org.id
    s.close()

    # force the review to flag so a WikiReview is queued (not auto-applied)
    from types import SimpleNamespace

    monkeypatch.setattr(
        review_mod,
        "outline_change",
        lambda *a, **k: SimpleNamespace(flags=["contradiction"], text="flagged"),
    )
    monkeypatch.setattr(app_mod, "_verify_wiki_write", lambda *a, **k: None)
    try:
        db = app_mod._SessionFactory()
        content = "# Topic\n\nA claim.\n"
        applied = app_mod.propose_wiki_write(
            db,
            org_id=org_id,
            proposed_by=1,
            slug="topic",
            content=content,
            target_scope="org",
            source_conversation_id=42,
            source_message_id=7,
        )
        db.commit()
        assert applied is False  # flagged -> queued
        rev = db.query(WikiReview).first()
        assert rev.provenance_hash == hashlib.sha256(content.encode()).hexdigest()
        assert rev.source_conversation_id == 42 and rev.source_message_id == 7
        db.close()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_parse_chat_ref_extracts_conversation_and_message_ids():
    from anthill.web.snippets import parse_chat_ref

    # a chat snippet's ref links back to the exact turn
    assert parse_chat_ref("conv:42/msg:7") == (42, 7)
    # a conversation without a specific message still links the chat
    assert parse_chat_ref("conv:5") == (5, None)
    # non-chat sources and junk carry no chat link
    assert parse_chat_ref("task:3") == (None, None)
    assert parse_chat_ref("") == (None, None)
    assert parse_chat_ref("conv:x/msg:y") == (None, None)
