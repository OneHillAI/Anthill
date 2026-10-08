"""Web search defaults ON for a new account, and the page tells the truth about it.

Founder decision, 2026-10-08: most questions are about live information, so web search starts on. It
sends the query text to a third-party search provider (anthill/search/web.py), so the Solo header says
"Web search is on for this chat" while it is on and only claims "Nothing leaves it" while it is off. An
account that turned web access off in Settings gets it off. Org conversations were already on.
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


def test_solo_chat_renders_web_search_checked_by_default(tmp_path, monkeypatch):
    # Founder decision, 2026-10-08: web search is ON by default for a new account (most questions are about
    # live information). This replaces the older Solo default of off.
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert '<input type="checkbox" id="opt-web" checked>' in r.text


def test_solo_chat_web_search_unchecked_when_user_turned_it_off(tmp_path, monkeypatch):
    # If the user turned "Web access" off in Settings -> Privacy (User.web_access_on), a new Solo chat seeds
    # the Web-search toggle OFF. Org chats are unaffected either way.
    from anthill.web.db import User

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == user_id).first().web_access_on = False
    s.commit()
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    r = client.get(f"/chat/{cid}")
    assert '<input type="checkbox" id="opt-web" >' in r.text


def test_org_chat_still_defaults_web_search_checked(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="org")
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert '<input type="checkbox" id="opt-web" checked>' in r.text


def test_solo_chat_trust_banner_text_matches_the_web_default_server_side(tmp_path, monkeypatch):
    # The server-rendered text must be honest for the state it renders: web search on (the default) says so,
    # and only an account with web access off claims "Nothing leaves it". JS then follows the live checkbox.
    from anthill.web.db import User

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="solo")
    assert 'id="chat-trust-msg">Web search is on for this chat.<' in client.get(f"/chat/{cid}").text
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == user_id).first().web_access_on = False
    s.commit()
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


def test_project_chat_without_an_org_server_follows_the_web_default(tmp_path, monkeypatch):
    # A project chat (plane "team") in an install with no organisation server runs on the local model, so its
    # Web search follows the account default like a Solo chat: off when the account turned it off.
    from anthill.web.db import User

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, user_id, plane="team")
    page = client.get(f"/chat/{cid}").text
    assert '<input type="checkbox" id="opt-web" checked>' in page  # new account: on
    assert "var _WEB_FOLLOWS_DEFAULT = true" in page
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == user_id).first().web_access_on = False
    s.commit()
    page = client.get(f"/chat/{cid}").text
    assert '<input type="checkbox" id="opt-web" >' in page  # follows the account default off
    assert "var _WEB_FOLLOWS_DEFAULT = true" in page


def test_project_chat_on_the_shared_model_always_starts_on(tmp_path, monkeypatch):
    from anthill.web.db import User

    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(User).filter(User.id == user_id).first().web_access_on = False
    s.commit()
    monkeypatch.setattr("anthill.planes.is_org_mode", lambda cfg: True)
    cid = _mkconv(app_mod, org_id, user_id, plane="team")
    page = client.get(f"/chat/{cid}").text
    assert '<input type="checkbox" id="opt-web" checked>' in page  # the shared model: always on
    assert "var _WEB_FOLLOWS_DEFAULT = false" in page
