"""Owner-scoped access control (IDOR regression, same class as the #597 task-scoping fix).

Four endpoints fetched a resource by org_id only, with no owner check, so one org member could read,
delete, or modify another member's private snippet, personal memory, or chat message by guessing the
(sequential integer) id. Each is now owner-scoped. These tests lock it: the attacker is blocked and
the legitimate owner still succeeds.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _setup(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import (
        ChatMessage,
        Conversation,
        MemoryItem,
        Organization,
        Snippet,
        User,
    )

    for k in ("ANTHILL_ORG_WIKI", "ANTHILL_WIKI_ROOT", "ANTHILL_WORKSPACE", "ANTHILL_FILES_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k.lower()))
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
    a = User(org_id=org.id, email="a@x.com", role="admin", active=True)
    b = User(org_id=org.id, email="b@x.com", role="member", active=True)
    s.add_all([a, b])
    s.commit()
    mem = MemoryItem(
        org_id=org.id,
        user_id=a.id,
        scope="personal",
        kind="fact",
        text="A-SECRET-MEMORY",
        embedding="",
        source="manual",
    )
    snip = Snippet(
        org_id=org.id,
        user_id=a.id,
        content="A-SECRET-SNIPPET",
        question="",
        tags="",
        rationale="",
        source="manual",
        source_ref="",
        content_key="k1",
        scope="personal",
    )
    conv = Conversation(org_id=org.id, user_id=a.id, title="A-chat")
    s.add_all([mem, snip, conv])
    s.flush()
    msg = ChatMessage(
        conversation_id=conv.id,
        role="assistant",
        content="A-answer",
        model="m",
        cache_hit=False,
        wiki_slugs="",
        thumbs_up=False,
        provenance="",
    )
    s.add(msg)
    s.commit()
    ids = {"org": org.id, "mem": mem.id, "snip": snip.id, "conv": conv.id, "msg": msg.id}
    ca = TestClient(app_mod.app)
    ca.cookies.set("session_token", make_token(a.id, org.id, "admin"))
    cb = TestClient(app_mod.app)
    cb.cookies.set("session_token", make_token(b.id, org.id, "member"))
    s.close()
    return app_mod, ids, ca, cb


def test_member_cannot_exfiltrate_another_members_memory(tmp_path, monkeypatch):
    _app, ids, ca, cb = _setup(tmp_path, monkeypatch)
    assert (
        cb.post(
            f"/memory/{ids['mem']}/wiki", data={"target_scope": "personal"}, follow_redirects=False
        ).status_code
        == 404
    )
    assert (
        ca.post(
            f"/memory/{ids['mem']}/wiki", data={"target_scope": "personal"}, follow_redirects=False
        ).status_code
        == 302
    )  # owner still can


def test_member_cannot_exfiltrate_another_members_snippet(tmp_path, monkeypatch):
    _app, ids, ca, cb = _setup(tmp_path, monkeypatch)
    assert (
        cb.post(
            f"/snippets/{ids['snip']}/wiki",
            data={"target_scope": "personal"},
            follow_redirects=False,
        ).status_code
        == 404
    )
    assert (
        ca.post(
            f"/snippets/{ids['snip']}/wiki",
            data={"target_scope": "personal"},
            follow_redirects=False,
        ).status_code
        == 302
    )


def test_member_cannot_delete_another_members_snippet(tmp_path, monkeypatch):
    from anthill.web.db import Snippet

    app_mod, ids, ca, cb = _setup(tmp_path, monkeypatch)
    cb.post(f"/snippets/{ids['snip']}/delete", follow_redirects=False)
    s = app_mod._SessionFactory()
    assert s.get(Snippet, ids["snip"]) is not None  # attacker did NOT delete it
    s.close()
    ca.post(f"/snippets/{ids['snip']}/delete", follow_redirects=False)  # owner can
    s = app_mod._SessionFactory()
    assert s.get(Snippet, ids["snip"]) is None
    s.close()


def test_member_cannot_rate_another_members_message(tmp_path, monkeypatch):
    from anthill.web.db import ChatMessage

    app_mod, ids, ca, cb = _setup(tmp_path, monkeypatch)
    cb.post(
        f"/chat/{ids['conv']}/thumbs",
        data={"message_id": ids["msg"], "value": 1},
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    assert s.get(ChatMessage, ids["msg"]).thumbs_up is False  # attacker did NOT flip it
    s.close()
    ca.post(
        f"/chat/{ids['conv']}/thumbs",
        data={"message_id": ids["msg"], "value": 1},
        follow_redirects=False,
    )  # owner can
    s = app_mod._SessionFactory()
    assert s.get(ChatMessage, ids["msg"]).thumbs_up is True
    s.close()
