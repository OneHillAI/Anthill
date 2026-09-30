"""Pinning conversations (#294): a pinned chat sticks to the top of the chat list under a "Pinned"
section, toggled from the rail or the history page."""

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


def _mkconv(app_mod, org_id, user_id, title):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = Conversation(org_id=org_id, user_id=user_id, title=title)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def _pinned(app_mod, conv_id):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        return s.get(Conversation, conv_id).pinned
    finally:
        s.close()


def test_pin_toggles(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "Keep me handy")
    assert _pinned(app_mod, cid) is False
    client.post(f"/chat/{cid}/pin", follow_redirects=False)
    assert _pinned(app_mod, cid) is True
    client.post(f"/chat/{cid}/pin", follow_redirects=False)  # toggles back off
    assert _pinned(app_mod, cid) is False


def test_pinned_chat_surfaces_in_a_pinned_section(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    _mkconv(app_mod, org_id, user_id, "Ordinary chat")
    pin_id = _mkconv(app_mod, org_id, user_id, "Important chat")
    client.post(f"/chat/{pin_id}/pin", follow_redirects=False)
    r = client.get(f"/chat/{pin_id}")  # the rail renders on a chat page
    assert r.status_code == 200
    assert "Pinned" in r.text and "Important chat" in r.text


def test_rail_has_search_and_sort_tools_when_there_are_chats(tmp_path, monkeypatch):
    # With chats in the rail, the conversation list gains a client-side search box + a Recent/A-Z sort
    # toggle (collapsing is wired in JS on the section headings). The full archive stays at /chat/history.
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "Some chat")
    body = client.get(f"/chat/{cid}").text
    assert 'id="railSearch"' in body and 'class="rail-convsearch"' in body  # the search box
    assert 'id="railSort"' in body and ">Recent<" in body  # the Recent/A-Z sort toggle
    assert 'href="/chat/history"' in body  # search-everything still points at the full archive


def test_pin_returns_to_the_referring_page(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, "From history")
    r = client.post(
        f"/chat/{cid}/pin",
        headers={"referer": "http://testserver/chat/history?q=budget"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # only the local path + query is used (host dropped), never an open redirect
    assert r.headers["location"] == "/chat/history?q=budget"


def test_pin_only_affects_the_owners_chat(tmp_path, monkeypatch):
    client, app_mod, org_id, _user_id = _app(tmp_path, monkeypatch)
    from anthill.web.db import Conversation, User

    s = app_mod._SessionFactory()
    other = User(org_id=org_id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.flush()
    other_conv = Conversation(org_id=org_id, user_id=other.id, title="Not yours")
    s.add(other_conv)
    s.commit()
    other_id = other_conv.id
    s.close()
    r = client.post(f"/chat/{other_id}/pin", follow_redirects=False)
    assert r.status_code == 302  # no error, but...
    assert _pinned(app_mod, other_id) is False  # ...another user's chat is untouched
