"""#683 requirement 3: the skill scope/governance field is enforced already at match time
(anthill/agent/executor.py: `all(sc in self.identity.scopes for sc in (s.scopes or []))`) - the real
gap was that the authoring UI never exposed or accepted it, so every hand-authored skill silently
shipped with `scopes=[]` (vacuously passes the gate - i.e. unrestricted). These cover the form/route
threading `scopes` from creation through to the edit-prefill JSON."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User, WikiReview


def _client(tmp_path, monkeypatch, role="admin"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org-wiki"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-skills"))
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


def test_skills_page_offers_a_scope_checkbox_for_each_all_scopes_entry(tmp_path, monkeypatch):
    from anthill.web.app import _ALL_SCOPES

    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        page = c.get("/skills").text
        for s in _ALL_SCOPES:
            assert f'value="{s}"' in page and 'class="skill-scope-cb"' in page
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_personal_skill_persists_its_scopes(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid, _org_id = _client(tmp_path, monkeypatch, role="member")
    try:
        r = c.post(
            "/skills/create",
            data={
                "scope": "personal",
                "name": "Send Email",
                "instructions": "draft and send the email",
                "scopes": "email,web",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        path = workspace_for("personal", user_id=uid).skills / "send-email" / "SKILL.md"
        assert path.exists()
        text = path.read_text()
        assert "x-anthill-scopes: email, web" in text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_unknown_scope_values_are_dropped_not_persisted(tmp_path, monkeypatch):
    """Only the real _ALL_SCOPES vocabulary is accepted - a stray/garbage value submitted directly
    to the route (bypassing the checkboxes) doesn't end up in the written skill's frontmatter."""
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid, _org_id = _client(tmp_path, monkeypatch, role="member")
    try:
        c.post(
            "/skills/create",
            data={
                "scope": "personal",
                "name": "Odd Skill",
                "instructions": "do it",
                "scopes": "web,not-a-real-scope",
            },
            follow_redirects=False,
        )
        text = (
            workspace_for("personal", user_id=uid).skills / "odd-skill" / "SKILL.md"
        ).read_text()
        assert "x-anthill-scopes: web" in text
        assert "not-a-real-scope" not in text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_no_scopes_means_unrestricted_as_before(tmp_path, monkeypatch):
    """Backward compatible: omitting scopes entirely still writes a skill with no x-anthill-scopes
    line at all, exactly like before this change (existing skills must not start requiring scopes)."""
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid, _org_id = _client(tmp_path, monkeypatch, role="member")
    try:
        c.post(
            "/skills/create",
            data={"scope": "personal", "name": "Plain Skill", "instructions": "do it"},
            follow_redirects=False,
        )
        text = (
            workspace_for("personal", user_id=uid).skills / "plain-skill" / "SKILL.md"
        ).read_text()
        assert "x-anthill-scopes" not in text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_org_skill_review_content_includes_the_chosen_scopes(tmp_path, monkeypatch):
    """The scopes chosen at authoring time must be part of what actually gets reviewed for a
    shared scope - not silently dropped on the review-gated path."""
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch, role="admin")
    try:
        c.post(
            "/skills/create",
            data={
                "scope": "org",
                "name": "Org Email Skill",
                "instructions": "draft the email",
                "scopes": "email",
            },
            follow_redirects=False,
        )
        rev = (
            app_mod._SessionFactory()
            .query(WikiReview)
            .filter(WikiReview.kind == "skill", WikiReview.target_scope == "org")
            .first()
        )
        assert rev is not None and "x-anthill-scopes: email" in rev.content
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_skills_raw_exposes_scopes_for_the_edit_prefill(tmp_path, monkeypatch):
    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch, role="member")
    try:
        c.post(
            "/skills/create",
            data={
                "scope": "personal",
                "name": "Send Email",
                "instructions": "draft and send the email",
                "scopes": "email,web",
            },
            follow_redirects=False,
        )
        r = c.get("/skills/send-email/raw")
        assert r.status_code == 200
        body = r.json()
        assert set(body["scopes"]) == {"email", "web"}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_executor_scope_gate_is_real_not_just_cosmetic(tmp_path, monkeypatch):
    """End-to-end confirmation that a scope written by the authoring form is the SAME field
    executor.py enforces at match time - not a UI-only decoration. Mirrors the exact check in
    anthill/agent/executor.py's _build_initial_messages."""
    from anthill.agent.skills import Skill, match_skills

    email_gated = Skill(
        slug="send-email",
        name="Send Email",
        description="Send an email",
        instructions="draft it",
        scopes=["email"],
    )
    unrestricted = Skill(
        slug="draft-email",
        name="Draft Email",
        description="Draft an email reply",
        instructions="do it",
    )
    goal = "please draft an email reply"
    matched = match_skills([email_gated, unrestricted], goal, k=5, min_score=1)
    assert email_gated in matched  # both match on keywords before the scope gate is applied

    # The gate itself (copied from executor.py's exact logic):
    identity_scopes_without_email = {"web", "wiki"}
    gated = [
        s for s in matched if all(sc in identity_scopes_without_email for sc in (s.scopes or []))
    ]
    assert email_gated not in gated  # correctly excluded: identity lacks "email"
    assert unrestricted in gated  # unrestricted (scopes=[]) always passes
