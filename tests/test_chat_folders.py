"""Chat folders (#294): a user groups their own conversations into folders, shown as collapsible
sections in the rail. Moving/creating/deleting is owner-scoped."""

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


def _folders(app_mod, user_id):
    from anthill.web.db import Folder

    s = app_mod._SessionFactory()
    try:
        rows = s.query(Folder).filter(Folder.user_id == user_id).order_by(Folder.id).all()
        return [(f.id, f.name) for f in rows]
    finally:
        s.close()


def _conv_folder(app_mod, conv_id):
    """Return (exists, folder_id) for a conversation."""
    from anthill.web.db import Conversation

    s = app_mod._SessionFactory()
    try:
        c = s.get(Conversation, conv_id)
        return (c is not None, c.folder_id if c else None)
    finally:
        s.close()


def test_create_folder(tmp_path, monkeypatch):
    client, app_mod, _org, user_id = _app(tmp_path, monkeypatch)
    client.post("/folders/new", data={"name": "Work"}, follow_redirects=False)
    client.post("/folders/new", data={"name": "  "}, follow_redirects=False)  # blank is ignored
    assert [name for _id, name in _folders(app_mod, user_id)] == ["Work"]


def test_move_chat_into_folder_and_unfile(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    client.post("/folders/new", data={"name": "Projects"}, follow_redirects=False)
    fid = _folders(app_mod, user_id)[0][0]
    cid = _mkconv(app_mod, org_id, user_id, "A filed chat")
    client.post(f"/chat/{cid}/folder", data={"folder_id": str(fid)}, follow_redirects=False)
    assert _conv_folder(app_mod, cid) == (True, fid)
    client.post(f"/chat/{cid}/folder", data={"folder_id": ""}, follow_redirects=False)  # unfile
    assert _conv_folder(app_mod, cid) == (True, None)


def test_folder_and_its_chat_render_in_the_rail(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    client.post("/folders/new", data={"name": "Roadmap"}, follow_redirects=False)
    fid = _folders(app_mod, user_id)[0][0]
    cid = _mkconv(app_mod, org_id, user_id, "Filed conversation")
    client.post(f"/chat/{cid}/folder", data={"folder_id": str(fid)}, follow_redirects=False)
    r = client.get(f"/chat/{cid}")
    assert r.status_code == 200
    assert "Roadmap" in r.text and "Filed conversation" in r.text


def test_delete_folder_unfiles_its_chats_not_deletes_them(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    client.post("/folders/new", data={"name": "Temp"}, follow_redirects=False)
    fid = _folders(app_mod, user_id)[0][0]
    cid = _mkconv(app_mod, org_id, user_id, "Chat in Temp")
    client.post(f"/chat/{cid}/folder", data={"folder_id": str(fid)}, follow_redirects=False)
    client.post(f"/folders/{fid}/delete", follow_redirects=False)
    assert _folders(app_mod, user_id) == []  # folder gone
    assert _conv_folder(app_mod, cid) == (True, None)  # chat kept, just unfiled


def test_folder_ops_are_owner_scoped(tmp_path, monkeypatch):
    client, app_mod, org_id, user_id = _app(tmp_path, monkeypatch)
    from anthill.web.db import Conversation, Folder, User

    s = app_mod._SessionFactory()
    other = User(org_id=org_id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.flush()
    other_folder = Folder(org_id=org_id, user_id=other.id, name="Their folder")
    other_conv = Conversation(org_id=org_id, user_id=other.id, title="Their chat")
    s.add_all([other_folder, other_conv])
    s.commit()
    other_fid, other_cid, other_uid = other_folder.id, other_conv.id, other.id
    s.close()

    mine = _mkconv(app_mod, org_id, user_id, "My chat")
    # I cannot file my chat into another user's folder (it stays unfiled).
    client.post(f"/chat/{mine}/folder", data={"folder_id": str(other_fid)}, follow_redirects=False)
    assert _conv_folder(app_mod, mine) == (True, None)
    # I cannot delete another user's folder, nor touch their chat's filing.
    client.post(f"/folders/{other_fid}/delete", follow_redirects=False)
    assert len(_folders(app_mod, other_uid)) == 1
    client.post(f"/chat/{other_cid}/folder", data={"folder_id": ""}, follow_redirects=False)
    assert _conv_folder(app_mod, other_cid)[0] is True  # untouched, still exists
