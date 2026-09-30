"""The Chat History page (#294): a full, searchable, paginated conversation list so that no chat is
lost behind the rail's recent-only cap. Mirrors the TestClient harness used in test_password_reset."""

from datetime import datetime, timedelta, timezone

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


def _mkconv(app_mod, org_id, user_id, title, plane="solo", days_ago=0, message=None):
    from anthill.web.db import ChatMessage, Conversation

    s = app_mod._SessionFactory()
    try:
        when = datetime.now(timezone.utc) - timedelta(days=days_ago)
        c = Conversation(
            org_id=org_id,
            user_id=user_id,
            title=title,
            plane=plane,
            created_at=when,
            updated_at=when,
        )
        s.add(c)
        s.flush()
        if message:
            s.add(ChatMessage(conversation_id=c.id, role="user", content=message))
        s.commit()
        return c.id
    finally:
        s.close()  # closing every session keeps SQLite from lock-contending under many convs


def test_history_reaches_chats_beyond_the_rail_cap(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    # 45 conversations: more than the 30-chat rail cap AND more than one 40-per-page page.
    for i in range(45):
        _mkconv(app_mod, org_id, user_id, f"Chat number {i:02d}", days_ago=i)
    r1 = client.get("/chat/history")
    assert r1.status_code == 200
    assert "45 conversations" in r1.text
    assert "Chat number 00" in r1.text  # newest, page 1
    assert "Chat number 44" not in r1.text  # oldest is off page 1 (past the old 30-cap too)
    r2 = client.get("/chat/history?page=2")
    assert r2.status_code == 200
    assert "Chat number 44" in r2.text  # but nothing is lost - it is on page 2


def test_search_matches_title(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _mkconv(app_mod, org_id, user_id, "Quarterly budget review")
    _mkconv(app_mod, org_id, user_id, "Vacation planning")
    r = client.get("/chat/history", params={"q": "budget"})
    assert r.status_code == 200
    assert "Quarterly budget review" in r.text
    assert "Vacation planning" not in r.text


def test_search_matches_message_content(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _mkconv(app_mod, org_id, user_id, "Untitled A", message="tell me about zebrafish genomes")
    _mkconv(app_mod, org_id, user_id, "Untitled B", message="what is the capital of France")
    r = client.get("/chat/history", params={"q": "zebrafish"})
    assert r.status_code == 200
    assert "Untitled A" in r.text  # matched on message text, not the title
    assert "Untitled B" not in r.text


def test_history_groups_by_recency(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _mkconv(app_mod, org_id, user_id, "Fresh chat", days_ago=0)
    _mkconv(app_mod, org_id, user_id, "Ancient chat", days_ago=90)
    r = client.get("/chat/history")
    assert r.status_code == 200
    assert "Today" in r.text and "Older" in r.text


def test_history_only_shows_the_signed_in_users_chats(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    from anthill.web.db import Conversation, User

    s = app_mod._SessionFactory()
    other = User(org_id=org_id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.flush()
    s.add(Conversation(org_id=org_id, user_id=other.id, title="Someone else private chat"))
    s.commit()
    s.close()
    _mkconv(app_mod, org_id, user_id, "My own chat")
    r = client.get("/chat/history")
    assert "My own chat" in r.text
    assert "Someone else private chat" not in r.text
