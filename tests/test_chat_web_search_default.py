"""Web search must default OFF on Solo chats.

A Solo conversation's header claims "Running on this machine. Nothing leaves it." while the chat's Web
search toggle previously defaulted ON - and turning it on genuinely sends the query text to a third-party
search provider (anthill/search/web.py), contradicting the banner. Org conversations already run on a
shared, non-local model, so their default is unaffected.
"""

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


def _mkconv(app_mod, org_id, user_id, *, plane="solo", title="Chat"):
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = Conversation(org_id=org_id, user_id=user_id, title=title, plane=plane)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def test_solo_chat_renders_web_search_unchecked_by_default(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert '<input type="checkbox" id="opt-web" >' in r.text


def test_solo_chat_web_search_checked_when_user_opted_in(tmp_path, monkeypatch):
    # If the user turned on "Web access" in Settings -> Privacy (User.web_access_on), a new Solo chat seeds
    # the Web-search toggle ON - overriding the privacy default. Org chats are unaffected either way.
    from anthill.web.db import User

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == user_id).first().web_access_on = True
    s.commit()
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    r = client.get(f"/chat/{cid}")
    assert '<input type="checkbox" id="opt-web" checked>' in r.text


def test_org_chat_still_defaults_web_search_checked(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="org")
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert '<input type="checkbox" id="opt-web" checked>' in r.text


def test_solo_chat_trust_banner_still_claims_nothing_leaves_it_server_side(tmp_path, monkeypatch):
    # The server-rendered default text must still be the honest one for the (default) off state - JS
    # only needs to override it when the user has actually turned web search on.
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    r = client.get(f"/chat/{cid}")
    assert 'id="chat-trust-msg">Running on this machine. Nothing leaves it.<' in r.text


def test_org_chat_shows_org_model_label_not_trust_banner(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="org")
    r = client.get(f"/chat/{cid}")
    assert (
        'id="chat-trust-msg"' not in r.text
    )  # the JS still references it by id, but no such element
    assert "org model" in r.text
