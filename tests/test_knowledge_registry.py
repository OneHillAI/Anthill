"""Phase 7 of #683: the DB registry (`KnowledgeItem`) that indexes wiki pages/skills currently on
disk, additive over the existing markdown files (spec requirement 6). Hooked into as few choke points
as possible: `propose_wiki_write()`'s auto-apply branch, `write_skill()`, `approve_review()`'s per-kind
branches, and `skills_delete()` - these tests exercise those hooks directly, plus the registry module's
own upsert/remove/backfill functions in isolation."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web.db import AuditLog, KnowledgeItem, Organization, User, WikiReview
from anthill.web.knowledge_registry import backfill_workspace, remove, sync_page, sync_skill

# ── sync_page / sync_skill / remove: unit-level upsert behavior ─────────────────


def _session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    from fk_seed import seed_org_and_users

    seed_org_and_users(s)
    s.commit()
    return s


def test_sync_page_inserts_then_upserts_in_place(tmp_path):
    s = _session(tmp_path)
    item = sync_page(
        s, org_id=1, scope="org", team_id=None, slug="alpha", title="Alpha", path="/a.md", user_id=1
    )
    assert item.id is not None and item.kind == "page" and item.review_state == "approved"
    same_id = item.id

    # a second sync for the same (org, scope, team, kind, slug) updates the existing row, not a dupe
    item2 = sync_page(
        s,
        org_id=1,
        scope="org",
        team_id=None,
        slug="alpha",
        title="Alpha v2",
        path="/a.md",
        user_id=2,
    )
    assert item2.id == same_id
    assert s.query(KnowledgeItem).count() == 1
    assert item2.title == "Alpha v2" and item2.last_editor_id == 2


def test_sync_skill_and_remove(tmp_path):
    s = _session(tmp_path)
    item = sync_skill(
        s,
        org_id=1,
        scope="personal",
        team_id=None,
        slug="my-skill",
        title="My Skill",
        path="/skills/my-skill/SKILL.md",
        user_id=1,
    )
    assert s.query(KnowledgeItem).filter_by(kind="skill", slug="my-skill").count() == 1

    remove(s, org_id=1, scope="personal", team_id=None, kind="skill", slug="my-skill")
    assert s.query(KnowledgeItem).count() == 0
    assert item.id is not None  # sanity: the row we removed had actually been created


def test_remove_is_a_noop_for_an_unregistered_item(tmp_path):
    s = _session(tmp_path)
    remove(s, org_id=1, scope="org", team_id=None, kind="page", slug="never-existed")  # no error
    assert s.query(KnowledgeItem).count() == 0


# ── propose_wiki_write's auto-apply branch registers a page ─────────────────────


class _FakeCleanBackend:
    def chat(self, messages):
        return '{"summary":"ok","flags":[]}'


def _clean_write_setup(tmp_path, monkeypatch):
    """A clean agent review + a verifier that can't run (returns None) - propose_wiki_write's
    auto-apply path, matching the convention in test_verify_wiki.py."""
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    owner = User(org_id=org.id, email="o@acme.com", role="admin", active=True)
    s.add(owner)
    s.commit()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeCleanBackend())
    monkeypatch.setattr(app_mod, "_verify_wiki_write", lambda cfg, *, content, source: None)
    return s, org.id, owner.id


def test_clean_wiki_write_auto_apply_registers_a_page(tmp_path, monkeypatch):
    s, org_id, uid = _clean_write_setup(tmp_path, monkeypatch)
    applied = app_mod.propose_wiki_write(
        s,
        org_id=org_id,
        proposed_by=uid,
        slug="alpha",
        content="# Alpha\n\nA fact.\n",
        target_scope="org",
    )
    s.commit()
    assert applied is True
    item = (
        s.query(KnowledgeItem)
        .filter_by(org_id=org_id, scope="org", kind="page", slug="alpha")
        .first()
    )
    assert item is not None
    assert item.title == "Alpha"  # from the page's H1
    assert item.last_editor_id == uid


def test_flagged_wiki_write_does_not_register_anything(tmp_path, monkeypatch):
    """The registry only reflects what's actually live - a queued (not yet approved) review must not
    appear, or it would misrepresent the wiki's real current contents."""
    from types import SimpleNamespace

    import anthill.wiki.review as review_mod

    s, org_id, uid = _clean_write_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        review_mod,
        "outline_change",
        lambda *a, **k: SimpleNamespace(flags=["contradiction"], text="x"),
    )
    applied = app_mod.propose_wiki_write(
        s, org_id=org_id, proposed_by=uid, slug="beta", content="# Beta\n\nX.\n", target_scope="org"
    )
    s.commit()
    assert applied is False
    assert s.query(KnowledgeItem).count() == 0


# ── write_skill() registers a skill when given db/org_id; unaffected without them ──


def test_write_skill_registers_when_org_context_given(tmp_path):
    from anthill.agent.skills import write_skill

    s = _session(tmp_path)
    sk = write_skill(
        "My Skill",
        "does a thing",
        "when needed",
        "do the steps",
        scope="personal",
        directory=str(tmp_path / "skills"),
        user_id=1,
        db=s,
        org_id=1,
    )
    assert sk.registry_id is not None
    item = s.get(KnowledgeItem, sk.registry_id)
    assert item is not None and item.kind == "skill" and item.slug == "my-skill"


def test_write_skill_without_db_or_org_id_registers_nothing(tmp_path):
    """Backward-compatible: a builtin skill (no scope) or a caller that never passes db/org_id (every
    existing call site before this phase) behaves exactly as before - no registry row, no error."""
    from anthill.agent.skills import write_skill

    sk = write_skill(
        "Builtin One", "d", "w", "do it", directory=str(tmp_path / "skills")
    )  # no scope, no db/org_id
    assert sk.registry_id is None


# ── approve_review()'s per-kind branch registers + links the audit row ──────────


def _app_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, {"org": org.id, "admin": admin.id}


def test_approve_skill_review_registers_and_links_the_audit_row(tmp_path, monkeypatch):
    from anthill.agent.skills import skill_md

    client, ids = _app_client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=ids["org"],
        proposed_by=ids["admin"],
        slug="my-skill",
        kind="skill",
        content=skill_md("My Skill", "d", "w", "do the thing", tier="org"),
        target_scope="org",
        status="pending",
    )
    s.add(rev)
    s.commit()
    rid = rev.id

    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302

    s2 = app_mod._SessionFactory()
    item = (
        s2.query(KnowledgeItem).filter_by(org_id=ids["org"], kind="skill", slug="my-skill").first()
    )
    assert item is not None and item.title == "My Skill"

    row = s2.query(AuditLog).filter_by(event="skill.approved").first()
    assert row is not None
    assert row.registry_id == item.id  # the audit row resolves to the same registry row


def test_approve_page_review_registers_a_page_with_no_dangling_registry_id(tmp_path, monkeypatch):
    client, ids = _app_client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=ids["org"],
        proposed_by=ids["admin"],
        slug="ordinary",
        kind="page",
        content="# Ordinary\n\nbody\n",
        target_scope="org",
        status="pending",
    )
    s.add(rev)
    s.commit()
    rid = rev.id

    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302

    s2 = app_mod._SessionFactory()
    item = s2.query(KnowledgeItem).filter_by(kind="page", slug="ordinary").first()
    assert item is not None
    row = s2.query(AuditLog).filter_by(event="wiki.approved").first()
    assert row.registry_id == item.id


def test_skills_delete_removes_the_registry_row(tmp_path, monkeypatch):
    client, ids = _app_client(tmp_path, monkeypatch)
    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("org", org_id=ids["org"])
    ws.init()
    (ws.skills / "demo").mkdir(parents=True, exist_ok=True)
    (ws.skills / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
    s = app_mod._SessionFactory()
    sync_skill(
        s,
        org_id=ids["org"],
        scope="org",
        team_id=None,
        slug="demo",
        title="Demo",
        path=str(ws.skills / "demo" / "SKILL.md"),
        user_id=ids["admin"],
    )
    assert s.query(KnowledgeItem).filter_by(kind="skill", slug="demo").count() == 1

    r = client.post("/skills/demo/delete", data={"scope": "org"}, follow_redirects=False)
    assert r.status_code == 302
    s2 = app_mod._SessionFactory()
    assert s2.query(KnowledgeItem).filter_by(kind="skill", slug="demo").count() == 0


# ── backfill_workspace: fills gaps, never touches what's already registered ─────


def test_backfill_registers_unregistered_items_and_skips_registered_ones(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    s = _session(tmp_path)
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    ws = workspace_for("personal", user_id=1)
    ws.init()
    ws.write_page("existing-page", "# Existing Page\n\nold content on disk\n")
    (ws.skills / "a-skill").mkdir(parents=True, exist_ok=True)
    (ws.skills / "a-skill" / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: d\n---\n\nbody\n"
    )
    # pre-register the page with a DIFFERENT title, to prove backfill does not touch it
    sync_page(
        s,
        org_id=1,
        scope="personal",
        team_id=None,
        slug="existing-page",
        title="Manually Set Title",
        path="stale/path.md",
        user_id=1,
    )

    added = backfill_workspace(s, ws, org_id=1, scope="personal", user_id=1)
    assert added == 1  # only the skill was missing; the page was already registered

    page_item = s.query(KnowledgeItem).filter_by(kind="page", slug="existing-page").first()
    assert page_item.title == "Manually Set Title"  # untouched, not overwritten
    skill_item = s.query(KnowledgeItem).filter_by(kind="skill", slug="a-skill").first()
    # "a-skill" is a conformant agentskills.io slug-shaped name, so parse_skill_md() prettifies it
    # into a display title ("A Skill") - see its docstring for why (#683 phase 3).
    assert skill_item is not None and skill_item.title == "A Skill"

    # running it again is a no-op (idempotent)
    assert backfill_workspace(s, ws, org_id=1, scope="personal", user_id=1) == 0
    assert s.query(KnowledgeItem).count() == 2
