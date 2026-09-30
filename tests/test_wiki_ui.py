"""Per-level wiki UI: pages / skills / principles surfaces with role-gated edits
and the 'Active for you' panel. Workspace roots are pointed at tmp so tests never
touch real data."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, Team, TeamMembership, User


def _client(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "orgwiki"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-skills"))
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role=role, active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, u.id


def test_personal_wiki_renders_saves_and_shows_active(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, _org_id, uid = _client(tmp_path, monkeypatch, role="member")
    try:
        # Pages is the default sub-tab; principles editor lives on the Principles sub-tab
        assert c.get("/wiki").status_code == 200
        r = c.get("/wiki?tab=principles")
        assert r.status_code == 200 and "Guiding principles" in r.text
        assert "Active for you" not in r.text  # nothing governing yet

        c.post(
            "/wiki/principles",
            data={"scope": "personal", "content": "Be terse."},
            follow_redirects=False,
        )
        assert "Be terse." in workspace_for("personal", user_id=uid).principles_md.read_text()
        assert "Active for you" in c.get("/wiki?tab=principles").text  # now it shows

        c.post(
            "/skills/create",  # skills moved to the unified scope-aware Skills page
            data={
                "scope": "personal",
                "name": "My Skill",
                "when_to_use": "x",
                "instructions": "do it",
            },
            follow_redirects=False,
        )
        assert (workspace_for("personal", user_id=uid).skills / "my-skill" / "SKILL.md").exists()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_org_wiki_admin_only(tmp_path, monkeypatch):
    c, app_mod, _org_id, _uid = _client(tmp_path, monkeypatch, role="member")
    try:
        assert c.get("/wiki/org").status_code == 403
        assert c.post("/wiki/principles", data={"scope": "org", "content": "x"}).status_code == 403
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_org_wiki_admin_edit_queues_review(tmp_path, monkeypatch):
    """Org principles now route through the agent review gate (v2). With no model
    available the change fails safe to a queued review instead of a silent write."""
    from anthill.web.db import WikiReview

    c, app_mod, org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        assert c.get("/wiki/org").status_code == 200
        c.post(
            "/wiki/principles",
            data={"scope": "org", "content": "Org rule."},
            follow_redirects=False,
        )
        s = app_mod._SessionFactory()
        rev = (
            s.query(WikiReview)
            .filter(
                WikiReview.org_id == org_id,
                WikiReview.kind == "principles",
                WikiReview.target_scope == "org",
            )
            .first()
        )
        assert rev is not None and rev.status == "pending" and "Org rule." in rev.content
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_team_wiki_owner_edit_queues_review(tmp_path, monkeypatch):
    from anthill.web.db import WikiReview

    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch, role="member")
    s = app_mod._SessionFactory()
    t = Team(org_id=org_id, name="P", slug="p", owner_id=uid)
    s.add(t)
    s.flush()
    s.add(TeamMembership(team_id=t.id, user_id=uid, role="owner", status="active"))
    s.commit()
    try:
        assert c.get(f"/wiki/team/{t.id}").status_code == 200
        c.post(
            "/wiki/principles",
            data={"scope": "team", "team_id": str(t.id), "content": "Team rule."},
            follow_redirects=False,
        )
        rev = (
            s.query(WikiReview)
            .filter(
                WikiReview.team_id == t.id,
                WikiReview.kind == "principles",
                WikiReview.target_scope == "team",
            )
            .first()
        )
        assert rev is not None and rev.status == "pending" and "Team rule." in rev.content
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


# ── "compute isn't set up yet" banner (declutter pass) ──────────────────────────


def test_wiki_banner_shown_for_unset_up_org_admin(tmp_path, monkeypatch):
    c, app_mod, _org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        r = c.get("/wiki")
        assert "Your compute isn't set up yet" in r.text
        assert '<a href="/settings/organization">Finish setup</a>' in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_banner_ask_an_admin_for_unset_up_member(tmp_path, monkeypatch):
    c, app_mod, _org_id, _uid = _client(tmp_path, monkeypatch, role="member")
    try:
        r = c.get("/wiki")
        assert "Your compute isn't set up yet" in r.text
        assert "Ask an admin to finish setup" in r.text
        assert 'href="/settings/organization"' not in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_banner_hidden_once_org_backend_validated(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    c, app_mod, org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        s = app_mod._SessionFactory()
        s.add(
            OrgSettings(
                org_id=org_id,
                org_backend_status="validated",
                org_model_endpoint="https://gpu.acme.example/v1",
            )
        )
        s.commit()
        r = c.get("/wiki")
        assert "Your compute isn't set up yet" not in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_banner_shown_for_unset_up_solo(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    c, app_mod, org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        s = app_mod._SessionFactory()
        s.add(OrgSettings(org_id=org_id, deployment_topology="solo", local_model_chosen=False))
        s.commit()
        r = c.get("/wiki")
        assert "Your compute isn't set up yet" in r.text
        assert '<a href="/setup/model">Finish setup</a>' in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_banner_hidden_for_solo_once_local_model_chosen(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    c, app_mod, org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        s = app_mod._SessionFactory()
        s.add(
            OrgSettings(
                org_id=org_id,
                deployment_topology="solo",
                local_model_chosen=True,
                solo_compute="local",
            )
        )
        s.commit()
        r = c.get("/wiki")
        assert "Your compute isn't set up yet" not in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_baseline_vs_advanced_split(tmp_path, monkeypatch):
    # Founder: uploading a document (or just chatting) should read as the automatic, zero-config
    # path; connector import and web research are real features but more deliberate setup, so they
    # live behind one "Advanced" disclosure instead of competing with Upload at equal visual weight.
    c, app_mod, _org_id, _uid = _client(tmp_path, monkeypatch, role="admin")
    try:
        body = c.get("/wiki").text
        add_doc_pos = body.index('id="wiki-add-doc"')
        advanced_pos = body.index('id="wiki-advanced"')
        research_pos = body.index('id="research-topics"')
        assert (
            add_doc_pos < advanced_pos < research_pos
        )  # Upload leads; Research sits inside Advanced
        assert '<details class="adv-disclosure" id="wiki-advanced">' in body
        assert "Advanced: more ways to add knowledge" in body
        assert "usually all you need" in body  # states the automatic-by-default framing explicitly
        # research-topics is unconditional, so it must be INSIDE the details element, not after it
        details_close = body.index("</details>")
        assert research_pos < details_close
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
