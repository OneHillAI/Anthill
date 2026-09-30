"""Skills authoring is a step-by-step wizard, not a single-page form with every field visible and a
live preview/validation output rendered below it (founder feedback: "the skill setup with the
questionnaire needs to be a wizard walkthrough and not a form that you fill out that provides the
output below"). All fields stay in the ONE real <form action="/skills/create"> with unchanged name=
attributes - this only restructures which step's fields are visible and adds a progress indicator, so
the existing server-side creation/validation tests (test_skill_scopes_authoring.py etc.) keep passing
unchanged. These tests cover the server-rendered wizard structure; the step-to-step JS behavior itself
(skwizGo/skwizNext/skwizBack/skwizSave/skwizRenderSummary) was verified live in a real browser."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _client(tmp_path, monkeypatch, role="admin"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

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
    return c, app_mod


def test_skills_page_renders_six_gated_steps_not_one_flat_form(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    try:
        body = c.get("/skills").text
        # exactly 6 step containers, each initially hidden except step 1
        for n in range(1, 7):
            assert f'<div class="skwiz-step" data-step="{n}"' in body
        assert '<div class="skwiz-step" data-step="1">' in body  # step 1 starts visible
        for n in range(2, 7):
            assert (
                f'<div class="skwiz-step" data-step="{n}" hidden>' in body
            )  # later steps start hidden
        # the step-indicator bar with all 6 labels
        for label in (
            "What it does",
            "The basics",
            "Instructions",
            "Who can use it",
            "Test it",
            "Save",
        ):
            assert label in body
        # the wizard driver functions exist
        for fn in (
            "skwizGo",
            "skwizNext",
            "skwizBack",
            "skwizSave",
            "skwizRenderSummary",
            "openSkillWizard",
        ):
            assert f"function {fn}" in body
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_summary_only_appears_on_the_final_step_not_below_the_open_form(tmp_path, monkeypatch):
    # The founder's exact complaint: a form you fill out that renders output below it. The summary/
    # validation area must live INSIDE step 6's container, not as a sibling visible from step 1.
    c, app_mod = _client(tmp_path, monkeypatch)
    try:
        body = c.get("/skills").text
        step6_start = body.index('<div class="skwiz-step" data-step="6" hidden>')
        step6_end = body.index("</form>", step6_start)
        summary_pos = body.index('id="skill-summary"')
        validation_pos = body.index('id="skill-validation"')
        assert step6_start < summary_pos < step6_end
        assert step6_start < validation_pos < step6_end
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_all_real_fields_keep_their_name_attributes_across_steps(tmp_path, monkeypatch):
    # The backend contract (skills_create()) must be untouched - restructuring into steps only
    # changes which step's div a field sits inside, never its name= attribute.
    c, app_mod = _client(tmp_path, monkeypatch)
    try:
        body = c.get("/skills").text
        for name in (
            "name",
            "scope",
            "team_id",
            "when_to_use",
            "description",
            "instructions",
            "scopes",
        ):
            assert f'name="{name}"' in body
        assert '<form method="post" action="/skills/create" id="skill-form">' in body
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_creation_still_works_end_to_end_through_the_restructured_form(tmp_path, monkeypatch):
    # A full POST to /skills/create must still succeed exactly as before - the wizard is presentation
    # only, proven by reusing the real creation path with data spanning what are now different steps.
    from anthill.wiki.workspace import workspace_for

    c, app_mod = _client(tmp_path, monkeypatch, role="member")
    try:
        r = c.post(
            "/skills/create",
            data={
                "scope": "personal",
                "name": "Weekly Investor Update",
                "when_to_use": "when asked to draft the investor update",
                "description": "Drafts our weekly investor update",
                "instructions": "Step 1: pull metrics. Step 2: draft.",
                "scopes": "web",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        path = workspace_for("personal", user_id=1).skills / "weekly-investor-update" / "SKILL.md"
        assert path.exists()
        assert "pull metrics" in path.read_text()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
