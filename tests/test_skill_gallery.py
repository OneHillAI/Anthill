"""#683 requirement 3: the vendored template gallery. `load_gallery()` reads
anthill/skills_gallery/ (separate from ANTHILL_SKILLS_DIR, so a gallery entry is inert until adopted),
filtered to an approved open license. `POST /skills/gallery/{slug}/adopt` copies an entry into a
target scope through the SAME write_skill()/review-gate path a hand-created skill uses - no bypass."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent.skills import gallery_dir, load_gallery
from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User, WikiReview


def _client(tmp_path, monkeypatch, role="member"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org-wiki"))
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
    user = User(org_id=org.id, email="u@acme.com", role=role, active=True)
    s.add_all([user, OrgSettings(org_id=org.id)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, role))
    return c, app_mod, user.id, org.id


# ── load_gallery() itself ──────────────────────────────────────────────────────


def test_real_gallery_dir_is_non_empty_and_all_apache_2():
    """The actual vendored gallery this PR ships - every entry passes the license filter, and there
    are at least the 3-6 entries the spec calls for."""
    entries = load_gallery()
    assert 3 <= len(entries) <= 6
    assert all(sk.license == "Apache-2.0" for sk in entries)
    assert all(sk.tier == "gallery" for sk in entries)


def test_gallery_dir_is_separate_from_the_builtin_skills_dir(monkeypatch, tmp_path):
    """A gallery entry must never leak into load_skills() (the builtin/scoped loader the executor and
    chat actually use) just by existing on disk - it stays inert until explicitly adopted."""
    from anthill.agent.skills import load_skills

    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-empty"))
    assert load_skills() == []
    assert load_gallery() != []  # the real package gallery is still found (different directory)


def test_license_filter_drops_unapproved_or_missing_license(tmp_path):
    folder = tmp_path / "gallery"
    (folder / "open-one").mkdir(parents=True)
    (folder / "open-one" / "SKILL.md").write_text(
        "---\nname: open-one\ndescription: d\nlicense: Apache-2.0\n---\nbody"
    )
    (folder / "no-license").mkdir(parents=True)
    (folder / "no-license" / "SKILL.md").write_text(
        "---\nname: no-license\ndescription: d\n---\nbody"
    )
    (folder / "proprietary").mkdir(parents=True)
    (folder / "proprietary" / "SKILL.md").write_text(
        "---\nname: proprietary\ndescription: d\nlicense: Source-available\n---\nbody"
    )
    entries = load_gallery(str(folder))
    assert {sk.slug for sk in entries} == {"open-one"}


def test_gallery_dir_helper_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHILL_SKILLS_GALLERY_DIR", str(tmp_path / "custom-gallery"))
    assert gallery_dir() == tmp_path / "custom-gallery"


# ── GET /skills/gallery ─────────────────────────────────────────────────────────


def test_gallery_list_route_returns_only_licensed_entries(tmp_path, monkeypatch):
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        r = c.get("/skills/gallery")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == len(load_gallery())
        assert all(e["license"] == "Apache-2.0" for e in body)
        assert {e["slug"] for e in body} >= {"brand-guidelines", "frontend-design"}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


# ── POST /skills/gallery/{slug}/adopt ────────────────────────────────────────────


def test_adopt_into_personal_writes_through_write_skill(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        r = c.post(
            "/skills/gallery/brand-guidelines/adopt",
            data={"scope": "personal"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        folder = workspace_for("personal", user_id=uid).skills / "brand-guidelines"
        assert (folder / "SKILL.md").exists()
        assert "brand-guidelines" in (folder / "SKILL.md").read_text()
        # attribution travels with the adopted copy
        assert (folder / "LICENSE.txt").exists()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_adopt_unknown_slug_404s(tmp_path, monkeypatch):
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        r = c.post(
            "/skills/gallery/not-a-real-entry/adopt",
            data={"scope": "personal"},
            follow_redirects=False,
        )
        assert r.status_code == 404
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_member_cannot_adopt_into_org_scope(tmp_path, monkeypatch):
    """Identical governance to /skills/create: adopting is not a way around the scope permission
    check a hand-authored skill already goes through."""
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch, role="member")
    try:
        r = c.post(
            "/skills/gallery/brand-guidelines/adopt",
            data={"scope": "org"},
            follow_redirects=False,
        )
        assert r.status_code == 403
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_admin_adopting_into_org_queues_the_same_review_gate(tmp_path, monkeypatch):
    """An org-scope adopt is not a bypass - it queues a WikiReview exactly like /skills/create does
    (the model backend is down in tests, so the review gate fails safe to a queued review, same as
    tests/test_skills_scoped.py::test_org_skill_queues_review)."""
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch, role="admin")
    try:
        c.post(
            "/skills/gallery/brand-guidelines/adopt", data={"scope": "org"}, follow_redirects=False
        )
        rev = (
            app_mod._SessionFactory()
            .query(WikiReview)
            .filter(WikiReview.kind == "skill", WikiReview.target_scope == "org")
            .first()
        )
        assert rev is not None and rev.status == "pending" and "brand-guidelines" in rev.content
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
