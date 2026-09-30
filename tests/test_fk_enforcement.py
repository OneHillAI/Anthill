"""Foreign keys are enforced (#634): an orphan row is rejected by the database, and the delete paths
that previously would have dangled a child now clean it up. The whole suite runs under enforcement;
these lock the property directly."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import ChatMessage, Conversation, Organization, User, get_engine


def test_foreign_keys_are_enforced_and_reject_an_orphan(tmp_path):
    eng = get_engine(tmp_path / "fk.db")
    db_mod.create_tables(eng)
    with eng.begin() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    s = sessionmaker(bind=eng)()
    s.add(Conversation(org_id=999, user_id=1, title="orphan"))  # org 999 does not exist
    with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
        s.commit()
    s.rollback()
    s.close()


def test_deleting_a_conversation_with_messages_succeeds_and_removes_them(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    for k in ("ANTHILL_ORG_WIKI", "ANTHILL_WIKI_ROOT", "ANTHILL_WORKSPACE", "ANTHILL_FILES_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k.lower()))
    eng = get_engine(tmp_path / "app.db")
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(u)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=u.id, title="C")
    s.add(conv)
    s.flush()
    s.add(
        ChatMessage(conversation_id=conv.id, role="user", content="hi", model="m", cache_hit=False)
    )
    s.commit()
    conv_id, uid, org_id = conv.id, u.id, org.id
    s.close()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "admin"))
    # Before #634 this raised (chat_messages still referenced the conversation); now it cascades.
    r = client.post(f"/chat/{conv_id}/delete", follow_redirects=False)
    assert r.status_code == 302

    s = app_mod._SessionFactory()
    assert s.get(Conversation, conv_id) is None
    assert s.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count() == 0
    s.close()
