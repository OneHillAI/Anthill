"""Chat grouping is the project: one grouping concept, no standalone folders.

Spec: docs/specs/consolidate-folders-into-projects.md. A chat is Unfiled (solo plane), in a Project (team
plane) or in the Organization (org plane). A chat can be attached to a project before its first prompt and
after it has history; in an org install the second needs an explicit yes, because the project runs on the
organization's cloud model. Everything is owner-scoped.
"""

import re

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
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
    s.flush()
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, org.id, user.id


def _mkconv(app_mod, org_id, user_id, title="Chat", plane="solo", team_id=None, messages=0):
    s = app_mod._SessionFactory()
    try:
        c = db_mod.Conversation(
            org_id=org_id, user_id=user_id, title=title, plane=plane, team_id=team_id
        )
        s.add(c)
        s.flush()
        for i in range(messages):
            role = "user" if i % 2 == 0 else "assistant"
            s.add(db_mod.ChatMessage(conversation_id=c.id, role=role, content=f"message {i}"))
        s.commit()
        return c.id
    finally:
        s.close()


def _mkteam(app_mod, org_id, owner_id, name="Platform", member_ids=()):
    s = app_mod._SessionFactory()
    try:
        t = db_mod.Team(org_id=org_id, name=name, slug=name.lower(), owner_id=owner_id)
        s.add(t)
        s.flush()
        for uid in member_ids:
            s.add(db_mod.TeamMembership(team_id=t.id, user_id=uid, role="owner", status="active"))
        s.commit()
        return t.id
    finally:
        s.close()


def _conv(app_mod, conv_id):
    """(plane, team_id, [message contents in order]) for a conversation."""
    s = app_mod._SessionFactory()
    try:
        c = s.get(db_mod.Conversation, conv_id)
        msgs = (
            s.query(db_mod.ChatMessage)
            .filter(db_mod.ChatMessage.conversation_id == conv_id)
            .order_by(db_mod.ChatMessage.id)
            .all()
        )
        return c.plane, c.team_id, [m.content for m in msgs]
    finally:
        s.close()


def _org_install(app_mod, org_id):
    """Make this an org install: an org backend has been configured (planes.is_org_mode)."""
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == org_id).first()
    cfg.org_backend_status = "validated"
    cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.commit()
    s.close()


def _post(client, conv_id, **form):
    return client.post(f"/chat/{conv_id}/project", data=form, follow_redirects=False)


# ── R1: no standalone folder concept left ────────────────────────────────────────────────


def test_no_folder_surface_is_left(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, uid, "A chat")
    page = client.get(f"/chat/{cid}").text
    for gone in ("New folder", "/folders/", "No folder", "Move to folder", "rail-folder"):
        assert gone not in page
    assert client.post("/folders/new", data={"name": "Work"}).status_code in (404, 405)
    assert client.post(f"/chat/{cid}/folder", data={"folder_id": "1"}).status_code in (404, 405)
    assert not hasattr(db_mod, "Folder")
    assert not hasattr(db_mod.Conversation, "folder_id")


# ── R2: a new project is visible at once; the control to attach is obvious ───────────────


def test_new_project_shows_in_the_rail_at_once_even_when_empty(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    cid = _mkconv(app_mod, org_id, uid, "A chat")
    assert 'class="rail-project"' not in client.get(f"/chat/{cid}").text  # none yet
    client.post("/teams", data={"name": "Roadmap"}, follow_redirects=False)
    page = client.get(f"/chat/{cid}").text
    assert 'class="rail-project"' in page and "<span>Roadmap</span>" in page
    assert "New chat in Roadmap" in page  # the "+" that starts a chat inside it
    assert 'href="/teams?new=1"' in page  # a visible "+ New project" in the rail
    # the chat header carries an obvious Project control, listing the project
    assert 'id="chat-project-select"' in page and ">Roadmap</option>" in page


def test_new_project_link_opens_the_create_form(tmp_path, monkeypatch):
    client, _app_mod, _org, _uid = _app(tmp_path, monkeypatch)
    page = client.get("/teams?new=1").text
    assert 'id="new-team"' in page and "new=1" in page


# ── R3: attach before the first prompt, and after it has history ────────────────────────


def test_attach_a_chat_before_its_first_prompt(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    tid = _mkteam(app_mod, org_id, uid, "Roadmap", [uid])
    cid = _mkconv(app_mod, org_id, uid, "Empty chat")
    r = _post(client, cid, team_id=str(tid))
    assert r.status_code == 302 and r.headers["location"] == f"/chat/{cid}"
    assert _conv(app_mod, cid) == ("team", tid, [])
    page = client.get(f"/chat/{cid}").text
    group = re.search(r'<details class="rail-project".*?</details>', page, re.S).group(0)
    assert "Empty chat" in group and f"/chat/{cid}" in group  # it shows under the project


def test_attach_a_chat_that_already_has_history_keeps_every_message(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)  # Solo: the project stays local
    tid = _mkteam(app_mod, org_id, uid, "Roadmap", [uid])
    cid = _mkconv(app_mod, org_id, uid, "Long chat", messages=6)
    before = _conv(app_mod, cid)[2]
    r = _post(client, cid, team_id=str(tid))
    assert r.headers["location"] == f"/chat/{cid}"  # no confirmation on a Solo install
    plane, team_id, after = _conv(app_mod, cid)
    assert (plane, team_id) == ("team", tid)
    assert after == before and len(after) == 6  # history intact, same order


def test_move_between_projects_and_detach_to_unfiled(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    a = _mkteam(app_mod, org_id, uid, "Alpha", [uid])
    b = _mkteam(app_mod, org_id, uid, "Beta", [uid])
    cid = _mkconv(app_mod, org_id, uid, "Moving chat", messages=4)
    _post(client, cid, team_id=str(a))
    _post(client, cid, team_id=str(b))
    assert _conv(app_mod, cid)[:2] == ("team", b)
    _post(client, cid, team_id="")  # remove from the project
    plane, team_id, msgs = _conv(app_mod, cid)
    assert (plane, team_id) == ("solo", None) and len(msgs) == 4  # Unfiled, not deleted
    _post(client, cid, team_id="")  # detaching an Unfiled chat is a no-op
    assert _conv(app_mod, cid)[:2] == ("solo", None)


# ── confirm first when the project runs on the org cloud model (privacy) ─────────────────


@pytest.mark.privacy_invariant
def test_org_install_asks_before_sending_a_chats_history_to_the_org_model(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    _org_install(app_mod, org_id)
    tid = _mkteam(app_mod, org_id, uid, "Platform", [uid])
    cid = _mkconv(app_mod, org_id, uid, "Private notes", messages=4)

    r = _post(client, cid, team_id=str(tid))  # no confirmation yet
    assert r.status_code == 302 and r.headers["location"] == f"/chat/{cid}?confirm_project={tid}"
    assert _conv(app_mod, cid)[:2] == ("solo", None)  # NOT moved: the history has not left Solo

    page = client.get(f"/chat/{cid}?confirm_project={tid}").text
    assert 'id="project-move-confirm"' in page and "organization's cloud model" in page
    assert "Move and continue" in page
    assert _conv(app_mod, cid)[:2] == ("solo", None)  # showing the question moves nothing

    r = _post(client, cid, team_id=str(tid), confirm="1")  # the user said yes
    assert r.headers["location"] == f"/chat/{cid}"
    assert _conv(app_mod, cid)[:2] == ("team", tid)


def test_org_install_does_not_ask_when_there_is_nothing_to_send(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    _org_install(app_mod, org_id)
    a = _mkteam(app_mod, org_id, uid, "Alpha", [uid])
    b = _mkteam(app_mod, org_id, uid, "Beta", [uid])
    empty = _mkconv(app_mod, org_id, uid, "Empty")
    assert _post(client, empty, team_id=str(a)).headers["location"] == f"/chat/{empty}"
    assert _conv(app_mod, empty)[:2] == ("team", a)  # empty chat: nothing to send, no question
    inproject = _mkconv(app_mod, org_id, uid, "Already in", plane="team", team_id=a, messages=4)
    assert _post(client, inproject, team_id=str(b)).headers["location"] == f"/chat/{inproject}"
    assert _conv(app_mod, inproject)[:2] == ("team", b)  # already on the org model: no new exposure


def test_confirm_banner_only_for_a_real_pending_move(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    _org_install(app_mod, org_id)
    mine = _mkteam(app_mod, org_id, uid, "Mine", [uid])
    foreign = _mkteam(app_mod, org_id, uid, "Foreign", [])  # I am not a member
    with_history = _mkconv(app_mod, org_id, uid, "History", messages=2)
    empty = _mkconv(app_mod, org_id, uid, "Empty")
    assert (
        'id="project-move-confirm"'
        in client.get(f"/chat/{with_history}?confirm_project={mine}").text
    )
    assert (
        'id="project-move-confirm"'
        not in client.get(f"/chat/{with_history}?confirm_project={foreign}").text
    )
    assert (
        'id="project-move-confirm"' not in client.get(f"/chat/{empty}?confirm_project={mine}").text
    )
    assert (
        'id="project-move-confirm"'
        not in client.get(f"/chat/{with_history}?confirm_project=9999").text
    )


# ── scoping, audit, and the Solo-local guarantee ─────────────────────────────────────────


def test_attach_is_owner_scoped_and_membership_scoped(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    other = db_mod.User(org_id=org_id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.commit()
    other_uid = other.id
    s.close()
    mine_team = _mkteam(app_mod, org_id, uid, "Mine", [uid])
    their_team = _mkteam(app_mod, org_id, other_uid, "Theirs", [other_uid])
    my_chat = _mkconv(app_mod, org_id, uid, "My chat", messages=2)
    their_chat = _mkconv(app_mod, org_id, other_uid, "Their chat", messages=2)
    org_chat = _mkconv(app_mod, org_id, uid, "Org chat", plane="org", messages=2)

    _post(client, my_chat, team_id=str(their_team))  # a project I do not belong to
    assert _conv(app_mod, my_chat)[:2] == ("solo", None)
    _post(client, their_chat, team_id=str(mine_team))  # a chat that is not mine
    assert _conv(app_mod, their_chat)[:2] == ("solo", None)
    _post(client, org_chat, team_id=str(mine_team))  # an Organization chat keeps its plane
    assert _conv(app_mod, org_chat)[:2] == ("org", None)
    assert _post(client, 99999, team_id=str(mine_team)).status_code == 302  # unknown chat


def test_attach_and_detach_are_audited(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    tid = _mkteam(app_mod, org_id, uid, "Roadmap", [uid])
    cid = _mkconv(app_mod, org_id, uid, "Chat", messages=2)
    _post(client, cid, team_id=str(tid))
    _post(client, cid, team_id="")
    s = app_mod._SessionFactory()
    rows = {
        r.event: r.detail
        for r in s.query(db_mod.AuditLog).filter(db_mod.AuditLog.event.like("chat.project_%")).all()
    }
    assert rows["chat.project_attach"] == f"conv={cid} from_team=0 to_team={tid}"
    assert rows["chat.project_detach"] == f"conv={cid} from_team={tid} to_team=0"


def test_a_solo_project_runs_fully_local(tmp_path):
    # R4: with no org backend configured, a project chat resolves to the local model: no "your cloud"
    # and no org endpoint needed, exactly like the old folder.
    from anthill.web.plane_routing import plane_inference
    from tests.test_plane_routing import _Cfg, _decrypt

    pi = plane_inference("team", _Cfg(), decrypt=_decrypt)
    assert pi.plane == "team" and pi.backend == "ollama" and pi.api_key is None
    assert pi.base_url.startswith("http://localhost:11434") and pi.wiki_scope == "team"


# ── the rail: Pinned, one group per project, Unfiled ─────────────────────────────────────


def test_rail_groups_chats_by_project_and_unfiled(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _app(tmp_path, monkeypatch)
    tid = _mkteam(app_mod, org_id, uid, "Roadmap", [uid])
    in_proj = _mkconv(app_mod, org_id, uid, "Project chat", plane="team", team_id=tid)
    loose = _mkconv(app_mod, org_id, uid, "Loose chat")
    page = client.get(f"/chat/{loose}").text
    group = re.search(r'<details class="rail-project".*?</details>', page, re.S).group(0)
    assert "Project chat" in group and "Loose chat" not in group
    unfiled = page.split('class="rail-conv-section">Unfiled<', 1)[1]
    assert "Loose chat" in unfiled and f"/chat/{in_proj}" not in unfiled.split("Organization")[0]


# ── R5: legacy databases ─────────────────────────────────────────────────────────────────


def test_fresh_databases_never_create_folders(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    db_mod.create_tables(eng)
    insp = inspect(eng)
    assert "folders" not in insp.get_table_names()
    assert "folder_id" not in {c["name"] for c in insp.get_columns("conversations")}


def test_migration_clears_legacy_folder_ids_and_chats_stay_writable(tmp_path):
    from anthill.web.migrate import run_migrations

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    db_mod.create_tables(eng)
    with eng.begin() as conn:  # rebuild the pre-change shape: folders table + FK column
        conn.execute(
            text(
                "CREATE TABLE folders (id INTEGER PRIMARY KEY, org_id INTEGER, user_id INTEGER, "
                "name VARCHAR(80), created_at DATETIME)"
            )
        )
        conn.execute(
            text("ALTER TABLE conversations ADD COLUMN folder_id INTEGER REFERENCES folders(id)")
        )
        conn.execute(text("INSERT INTO folders (id, name) VALUES (1, 'Work')"))
        conn.execute(
            text(
                "INSERT INTO conversations (title, plane, memory_mark, pinned, slack_thread, folder_id) "
                "VALUES ('Filed', 'solo', 0, 0, '', 1)"
            )
        )
        conn.execute(text("PRAGMA user_version = 4"))  # an install from before this migration
    ran = run_migrations(eng, fresh=False, before_change=lambda names: None)
    assert ran == [5]
    with eng.begin() as conn:
        assert conn.execute(text("SELECT folder_id FROM conversations")).scalar() is None
        assert conn.execute(text("SELECT title FROM conversations")).scalar() == "Filed"  # kept
        # the unused folders table stays: dropping it would break every later write (FK enforcement)
        assert conn.execute(text("SELECT count(*) FROM folders")).scalar() == 1
    s = sessionmaker(bind=eng)()
    s.add(db_mod.Conversation(title="New after migration", plane="solo"))  # must not raise
    s.commit()
    assert s.query(db_mod.Conversation).count() == 2
