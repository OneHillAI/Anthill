"""Chat defaults and the first-use notice (docs/specs/chat-thinking-toggle.md).

Settings holds the default for Thinking and Web search; each chat can change its own. The first time a user
opens chat, agents or tasks a one-time notice says what the defaults are and where to change them.
Model-free: pages are rendered with a TestClient.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
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
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    s.add(conv)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, conv.id, user.id


def _user(app_mod, user_id):
    from anthill.web.db import User

    s = app_mod._SessionFactory()
    try:
        u = s.query(User).filter(User.id == user_id).first()
        return u.thinking_on, u.web_access_on, u.chat_defaults_notice_seen
    finally:
        s.close()


def test_new_user_defaults(tmp_path, monkeypatch):
    _client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    assert _user(app_mod, uid) == (
        False,
        False,
        False,
    )  # thinking off, web off, notice not yet seen


def test_notice_shows_on_chat_agents_and_tasks_until_acknowledged(tmp_path, monkeypatch):
    client, _app_mod, cid, _uid = _app(tmp_path, monkeypatch)
    for path in (f"/chat/{cid}", "/agents", "/tasks"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert 'id="first-use-notice"' in r.text, path
        assert 'Thinking is <b id="fu-think-state">off</b>' in r.text, (
            path
        )  # new accounts: answers start fast
        assert '<b id="fu-web-state">off</b>' in r.text, path  # new accounts: web search off too
        assert "clearly needs live information" not in r.text, (
            path
        )  # no automatic search to disclose
        assert 'href="/personalize#privacy"' in r.text, (
            path
        )  # opens the Settings tab that holds the defaults
    assert client.post("/settings/chat-defaults", json={"seen": True}).json()["seen"] is True
    for path in (f"/chat/{cid}", "/agents", "/tasks"):
        assert 'id="first-use-notice"' not in client.get(path).text, path


def test_defaults_endpoint_saves_and_ignores_non_booleans(tmp_path, monkeypatch):
    client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    r = client.post("/settings/chat-defaults", json={"thinking_on": False, "web_access": False})
    assert r.json() == {"ok": True, "thinking_on": False, "web_access": False, "seen": False}
    assert _user(app_mod, uid) == (False, False, False)
    client.post(
        "/settings/chat-defaults", json={"thinking_on": "no", "web_access": 0, "seen": "yes"}
    )
    assert _user(app_mod, uid) == (
        False,
        False,
        False,
    )  # strings and numbers are not booleans: ignored
    assert client.post("/settings/chat-defaults", content=b"nope").status_code == 400
    assert client.post("/settings/chat-defaults", json=[1]).status_code == 400


def test_defaults_endpoint_needs_a_signed_in_user(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    _client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    anonymous = TestClient(app_mod.app, follow_redirects=False)
    r = anonymous.post("/settings/chat-defaults", json={"seen": True})
    assert r.status_code in (302, 303, 401, 403)
    assert _user(app_mod, uid)[2] is False


def test_chat_page_follows_the_saved_defaults(tmp_path, monkeypatch):
    client, _app_mod, cid, _uid = _app(tmp_path, monkeypatch)
    page = client.get(f"/chat/{cid}").text
    assert "think: false" in page  # new accounts: Thinking off, so an answer starts in seconds
    assert (
        '<span id="opt-think-label">Thinking off</span>' in page
    )  # the button is right before any script runs
    assert (
        'id="opt-think" class="opt-toggle" onclick="toggleThinking()" aria-pressed="false"' in page
    )
    assert '<input type="checkbox" id="opt-web" >' in page  # new accounts: web search off
    client.post("/settings/chat-defaults", json={"thinking_on": True, "web_access": True})
    page = client.get(f"/chat/{cid}").text
    assert "think: true" in page
    assert '<span id="opt-think-label">Thinking on</span>' in page
    assert '<input type="checkbox" id="opt-web" checked>' in page


def test_settings_page_has_the_thinking_default_and_saves_it(tmp_path, monkeypatch):
    client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    page = client.get("/personalize").text
    assert 'name="thinking_on"' in page and 'id="chat-defaults"' in page
    # an unticked box (absent from the form) turns the default off, a ticked one turns it on
    client.post("/personalize", data={"memory_on": "on"}, follow_redirects=False)
    assert _user(app_mod, uid)[0] is False
    client.post(
        "/personalize", data={"memory_on": "on", "thinking_on": "on"}, follow_redirects=False
    )
    assert _user(app_mod, uid)[0] is True


def test_notice_says_what_applies_on_each_page(tmp_path, monkeypatch):
    client, _app_mod, cid, _uid = _app(tmp_path, monkeypatch)
    keeps = "Agents and scheduled tasks keep their own settings."
    assert keeps not in client.get(f"/chat/{cid}").text  # chats are what the defaults govern
    for path in ("/agents", "/tasks"):
        assert keeps in client.get(path).text, path
    page = client.get("/tasks").text
    assert 'href="/personalize#model"' in page and 'href="/personalize#privacy"' in page


def test_thinking_default_lives_under_model_and_web_access_under_privacy(tmp_path, monkeypatch):
    client, _app_mod, _cid, _uid = _app(tmp_path, monkeypatch)
    page = client.get("/personalize").text
    model_panel = page.index('data-st="model">')
    knowledge_panel = page.index('class="st-panel" data-st="knowledge"')
    privacy_panel = page.index('class="st-panel" data-st="privacy"')
    assert model_panel < page.index('name="thinking_on"') < knowledge_panel
    assert page.index('name="web_access"') > privacy_panel


def test_what_leaves_card_admits_web_search_when_it_is_on(tmp_path, monkeypatch):
    client, _app_mod, _cid, _uid = _app(tmp_path, monkeypatch)
    page = client.get("/personalize").text
    assert "sent to a search provider" not in page  # a new account has web search off
    assert "Nothing</span>" in page  # and nothing leaves, which the pill says
    client.post("/settings/chat-defaults", json={"web_access": True})
    page = client.get("/personalize").text
    assert "Web search is on" in page and "sent to a search provider" in page
    client.post("/settings/chat-defaults", json={"web_access": False})
    page = client.get("/personalize").text
    assert "sent to a search provider" not in page


def test_org_chat_keeps_web_on_and_follows_the_thinking_default(tmp_path, monkeypatch):
    from anthill.web.db import Conversation

    client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    me = s.query(db_mod.User).filter(db_mod.User.id == uid).first()
    org_conv = Conversation(org_id=me.org_id, user_id=uid, plane="org")
    s.add(org_conv)
    s.commit()
    org_id = org_conv.id
    s.close()
    client.post("/settings/chat-defaults", json={"thinking_on": False, "web_access": False})
    page = client.get(f"/chat/{org_id}").text
    assert '<input type="checkbox" id="opt-web" checked>' in page  # org chats were always on
    assert "think: false" in page  # the Thinking default applies to organisation chats too
    assert (
        "var _WEB_FOLLOWS_DEFAULT = false" in page
    )  # so a changed web default never rewrites an org chat


def test_upgrading_an_existing_database_keeps_choices_and_fills_the_new_columns(tmp_path):
    from sqlalchemy import text

    eng = create_engine(
        f"sqlite:///{tmp_path / 'old.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = db_mod.Organization(name="Old", slug="old")
    s.add(org)
    s.flush()
    s.add(
        db_mod.User(
            org_id=org.id, email="old@x.com", role="member", active=True, web_access_on=False
        )
    )
    s.add(  # an account that already had web search switched on (set by hand, or created while it was on)
        db_mod.User(
            org_id=org.id, email="web@x.com", role="member", active=True, web_access_on=True
        )
    )
    s.commit()
    s.close()
    with eng.begin() as con:  # an install from before these columns existed
        con.execute(text("ALTER TABLE users DROP COLUMN thinking_on"))
        con.execute(text("ALTER TABLE users DROP COLUMN chat_defaults_notice_seen"))
    db_mod.create_tables(eng)  # what the app runs at startup
    rows = {r.email: r for r in sessionmaker(bind=eng)().query(db_mod.User).all()}
    assert (
        rows["old@x.com"].web_access_on is False
    )  # an existing account keeps the web value it had
    assert rows["web@x.com"].web_access_on is True  # including one that had it on
    for row in rows.values():
        # the new columns: an existing account keeps Thinking on, as it always had, and has not seen the notice
        assert row.thinking_on is True and row.chat_defaults_notice_seen is False


def test_notice_in_an_organisation_account_talks_about_personal_chats(tmp_path, monkeypatch):
    client, app_mod, _cid, uid = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    me = s.query(db_mod.User).filter(db_mod.User.id == uid).first()
    s.add(db_mod.OrgSettings(org_id=me.org_id))
    s.commit()
    s.close()
    page = client.get("/tasks").text
    assert "in your chats." in page and "Organisation chats always search the web." not in page
    monkeypatch.setattr("anthill.planes.is_org_mode", lambda cfg: True)
    page = client.get("/tasks").text
    assert "in your personal chats." in page
    assert "Organisation chats always search the web." in page


def test_thinking_is_described_as_local_only_everywhere_it_is_offered(tmp_path, monkeypatch):
    client, _app_mod, cid, _uid = _app(tmp_path, monkeypatch)
    for page in (
        client.get(f"/chat/{cid}").text,
        client.get("/tasks").text,
        client.get("/personalize").text,
    ):
        assert "Only " in page and "models running on this machine" in page


def test_the_settings_page_describes_both_defaults_as_off_for_a_new_account(tmp_path, monkeypatch):
    client, _app_mod, _cid, _uid = _app(tmp_path, monkeypatch)
    page = client.get("/personalize").text
    assert (
        page.count("Off by default for a new account") == 2
    )  # the Thinking card and the Web access card
    assert "On by default" not in page and "on by default" not in page
    assert "Nothing leaves unless you connect a cloud." in page  # true while web search is off
    assert "Web search is on" not in page and "sent to a search provider" not in page
