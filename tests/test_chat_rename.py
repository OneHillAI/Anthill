"""Renaming a conversation: the chat list and the open chat's header both used to offer only Delete
(and Pin, and Move to folder) - never a way to fix a bad auto-generated title. Owner-scoped like the
other per-chat actions in test_chat_folders.py."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, org.id, user.id


def _mkconv(app_mod, org_id, user_id, title="Chat"):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = Conversation(org_id=org_id, user_id=user_id, title=title)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def _conv_title(app_mod, conv_id):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = s.get(Conversation, conv_id)
        return c.title if c else None
    finally:
        s.close()


def test_rename_conversation(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "New conversation")
    client.post(
        f"/chat/{cid}/rename", data={"title": "  NDA draft, Virginia  "}, follow_redirects=False
    )
    assert _conv_title(app_mod, cid) == "NDA draft, Virginia"


def test_rename_ignores_blank_title(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "Keep me")
    client.post(f"/chat/{cid}/rename", data={"title": "   "}, follow_redirects=False)
    assert _conv_title(app_mod, cid) == "Keep me"


def test_rename_truncates_to_column_limit(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "Short")
    client.post(f"/chat/{cid}/rename", data={"title": "x" * 500}, follow_redirects=False)
    assert _conv_title(app_mod, cid) == "x" * 200


def test_rename_is_owner_scoped(tmp_path, monkeypatch):
    client, app_mod, org_id, _user_id = _app(tmp_path, monkeypatch)
    from anthill.web.db import Conversation, User

    s = app_mod._SessionFactory()
    other = User(org_id=org_id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.flush()
    other_conv = Conversation(org_id=org_id, user_id=other.id, title="Their chat")
    s.add(other_conv)
    s.commit()
    other_cid = other_conv.id
    s.close()

    client.post(f"/chat/{other_cid}/rename", data={"title": "Hijacked"}, follow_redirects=False)
    assert _conv_title(app_mod, other_cid) == "Their chat"  # untouched
