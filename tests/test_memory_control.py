"""Memory visibility + control: pause auto-memory, keep-personal opt-out, edit, provenance links;
plus snippet edit. Model-free (no embeddings needed - corroboration falls back to text match)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _engine(tmp_path, name="m.db"):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    _s = sessionmaker(bind=eng)()
    seed_org_and_users(_s, user_ids=(1, 2, 3))
    _s.commit()
    _s.close()
    return eng


def _session(tmp_path):
    return sessionmaker(bind=_engine(tmp_path), autoflush=False, autocommit=False)()


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = _engine(tmp_path, "app.db")
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(u)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return client, {"org": org.id, "u": u.id}


# ── auto-memory gate + corroboration control (unit) ─────────────────────────────


def test_auto_memory_on_reflects_user_flag(tmp_path):
    from anthill.web.db import User
    from anthill.web.memory_ops import auto_memory_on

    db = _session(tmp_path)
    u = User(org_id=1, email="x@y.com", role="member", active=True)
    db.add(u)
    db.commit()
    assert auto_memory_on(db, u.id) is True  # default: on
    u.auto_memory_off = True
    db.commit()
    assert auto_memory_on(db, u.id) is False  # paused


def test_corroborate_promotes_across_users(tmp_path):
    from anthill.web.db import MemoryItem
    from anthill.web.memory_ops import maybe_corroborate

    db = _session(tmp_path)
    a = MemoryItem(org_id=1, user_id=1, scope="personal", kind="fact", text="we use postgres")
    b = MemoryItem(org_id=1, user_id=2, scope="personal", kind="fact", text="we use postgres")
    db.add_all([a, b])
    db.commit()
    scope = maybe_corroborate(db, 1, b)  # b is the freshly stored one
    db.commit()
    assert scope == "org"  # no common team -> org-wide
    assert db.query(MemoryItem).filter(MemoryItem.id == a.id).first().scope == "org"


def test_corroborate_skips_kept_personal(tmp_path):
    from anthill.web.db import MemoryItem
    from anthill.web.memory_ops import maybe_corroborate

    db = _session(tmp_path)
    # b is kept-personal -> never promoted, even though user 1 has the same fact
    a = MemoryItem(org_id=1, user_id=1, scope="personal", kind="fact", text="we use postgres")
    b = MemoryItem(
        org_id=1,
        user_id=2,
        scope="personal",
        kind="fact",
        text="we use postgres",
        pinned_personal=True,
    )
    db.add_all([a, b])
    db.commit()
    assert maybe_corroborate(db, 1, b) is None
    assert db.query(MemoryItem).filter(MemoryItem.id == a.id).first().scope == "personal"


# ── #683 companion fix: a team-promoted memory must stay visible on the Memory page ─────────────


def test_team_promoted_memory_stays_visible_on_the_memory_page(tmp_path, monkeypatch):
    # memory_promote_team sets scope="team" and clears user_id - before the fix, the page query's
    # (user_id == uid) | (scope == "org") filter matched neither clause and the item silently
    # vanished from the list (recall still injected it into answers; only this page was broken).
    import anthill.web.app as app_mod
    from anthill.web.db import MemoryItem, Team, TeamMembership

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["org"], name="Acme Team", slug="acme-team", owner_id=ids["u"])
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=ids["u"], role="owner", status="active"))
    m = MemoryItem(
        org_id=ids["org"], user_id=ids["u"], scope="personal", kind="fact", text="team fact"
    )
    s.add(m)
    s.commit()
    mid, team_id = m.id, team.id

    r = client.post(f"/memory/{mid}/promote/team", data={"team_id": team_id})
    assert r.status_code in (200, 302)
    promoted = app_mod._SessionFactory().query(MemoryItem).filter(MemoryItem.id == mid).first()
    assert promoted.scope == "team" and promoted.user_id is None  # the mutation itself is unchanged

    page = client.get("/memory").text
    assert "team fact" in page  # the fix: still shown, not silently dropped


# ── #683 companion fix: pausing auto-memory must also stop training capture ─────────────────────


def test_pausing_auto_memory_stops_training_capture(tmp_path, monkeypatch):
    # Before the fix, record_example() in the chat route was gated only on "not ephemeral" - a user
    # who paused auto-memory (expecting Anthill to stop remembering things about them) still had every
    # full turn captured as training data, which is more revealing than a distilled memory item.
    import anthill.web.app as app_mod
    from anthill.web.db import Conversation, TrainingExample

    client, ids = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.chat_stream",
        lambda self, messages, **kwargs: iter(["answer"]),
    )
    client.post("/memory/auto-toggle", follow_redirects=False)  # pause

    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["u"], plane="solo")
    s.add(conv)
    s.commit()
    cid = conv.id

    client.get(f"/chat/{cid}/stream", params={"message": "hello"})
    assert app_mod._SessionFactory().query(TrainingExample).count() == 0  # off means off

    client.post("/memory/auto-toggle", follow_redirects=False)  # resume
    conv2 = Conversation(org_id=ids["org"], user_id=ids["u"], plane="solo")
    s.add(conv2)
    s.commit()
    client.get(f"/chat/{conv2.id}/stream", params={"message": "hello again"})
    assert app_mod._SessionFactory().query(TrainingExample).count() == 1  # capture resumes


# ── routes: toggle / keep-personal / edit / provenance / snippet edit ───────────


def test_memory_auto_toggle_and_keep_personal_and_edit(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import MemoryItem, User

    client, ids = _app(tmp_path, monkeypatch)

    # toggle pauses auto-memory
    client.post("/memory/auto-toggle", follow_redirects=False)
    u = app_mod._SessionFactory().query(User).filter(User.id == ids["u"]).first()
    assert u.auto_memory_off is True

    s = app_mod._SessionFactory()
    m = MemoryItem(
        org_id=ids["org"], user_id=ids["u"], scope="personal", kind="fact", text="old text"
    )
    s.add(m)
    s.commit()
    mid = m.id

    client.post(f"/memory/{mid}/keep-personal", follow_redirects=False)
    client.post(f"/memory/{mid}/edit", data={"text": "corrected text"}, follow_redirects=False)
    got = app_mod._SessionFactory().query(MemoryItem).filter(MemoryItem.id == mid).first()
    assert got.pinned_personal is True and got.text == "corrected text"


def test_memory_page_links_source_to_its_origin(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import MemoryItem

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(
        MemoryItem(
            org_id=ids["org"],
            user_id=ids["u"],
            scope="personal",
            kind="fact",
            text="from a chat",
            source="chat",
            source_id=42,
        )
    )
    s.commit()
    page = client.get("/memory").text
    assert 'href="/chat/42"' in page  # provenance link back to the source chat


def test_snippet_edit_updates_content_and_tags(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import Snippet

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    snip = Snippet(org_id=ids["org"], user_id=ids["u"], content="old", tags="a")
    s.add(snip)
    s.commit()
    sid = snip.id
    client.post(
        f"/snippets/{sid}/edit", data={"content": "new body", "tags": "x,y"}, follow_redirects=False
    )
    got = app_mod._SessionFactory().query(Snippet).filter(Snippet.id == sid).first()
    assert got.content == "new body" and got.tags == "x,y"
