"""#683 requirement 3: local, in-app validation for a hand-authored skill. `validate_skill()` uses
the open agentskills.io validation rules as its reference (the reference implementation itself is
demonstration-only and is deliberately not vendored - see docs/AGENT_SKILLS.md), checking naming, a
description nudge, a rough instructions token-budget heuristic, and dangling [[asset]] references."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent.skills import INSTRUCTIONS_WORD_BUDGET, Skill, conform_name, validate_skill
from anthill.web import db as db_mod
from anthill.web.db import Organization, User


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

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
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return c, app_mod


def _sk(**kw) -> Skill:
    base = {
        "slug": "x",
        "name": "X",
        "description": "d",
        "when_to_use": "w",
        "instructions": "1. do it",
    }
    base.update(kw)
    return Skill(**base)


def test_clean_skill_has_no_errors_or_warnings():
    result = validate_skill(_sk())
    assert result == {"errors": [], "warnings": []}


def test_empty_description_is_a_warning_not_an_error():
    # Must stay a warning, not an error: skill_md() already falls back to a generic description, and
    # existing skills created with no description (see tests/test_skills_scoped.py) must keep working.
    result = validate_skill(_sk(description=""))
    assert result["errors"] == []
    assert any("description" in w.lower() for w in result["warnings"])


def test_long_instructions_warn_and_are_honest_about_being_a_word_count():
    long_instructions = "word " * (INSTRUCTIONS_WORD_BUDGET + 50)
    result = validate_skill(_sk(instructions=long_instructions))
    assert result["errors"] == []
    warning = next(w for w in result["warnings"] if "long" in w.lower())
    assert str(INSTRUCTIONS_WORD_BUDGET) in warning
    assert "not an exact token count" in warning  # honest about being a rough heuristic


def test_short_instructions_do_not_warn():
    result = validate_skill(_sk(instructions="Short and to the point."))
    assert not any("long" in w.lower() for w in result["warnings"])


def test_dangling_asset_reference_warns():
    result = validate_skill(_sk(instructions="See [[reference/notes.md]] for details.", assets=[]))
    assert result["errors"] == []
    assert any("reference/notes.md" in w for w in result["warnings"])


def test_asset_reference_that_exists_does_not_warn():
    result = validate_skill(
        _sk(instructions="See [[reference/notes.md]] for details.", assets=["reference/notes.md"])
    )
    assert not any("reference/notes.md" in w for w in result["warnings"])


def test_bad_slug_is_an_error():
    # A slug that isn't conform_name()'d (e.g. a hand-built Skill, or one read from an external
    # source) should be caught, not silently accepted.
    result = validate_skill(_sk(slug="Not Conformant!"))
    assert any("name" in e.lower() for e in result["errors"])


def test_conformed_name_never_errors():
    result = validate_skill(_sk(slug=conform_name("A Perfectly Normal Skill Name")))
    assert result["errors"] == []


def test_validate_route_matches_the_pure_function(tmp_path):
    c, app_mod = _client(tmp_path)
    try:
        r = c.post(
            "/skills/validate",
            data={
                "name": "Weekly Update",
                "description": "",
                "when_to_use": "",
                "instructions": "do it",
                "scopes": "",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"errors", "warnings"}
        assert body["errors"] == []
        assert any("description" in w.lower() for w in body["warnings"])
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_skills_create_rejects_a_real_error_server_side(tmp_path, monkeypatch):
    """Never trust the client alone: even if a request somehow skips /skills/validate, skills_create()
    re-validates and refuses to save on a real error. In practice conform_name() always yields a
    valid slug from the authoring form, so this exercises the defense-in-depth path directly."""
    import anthill.agent.skills as skills_mod

    # skills_create() imports validate_skill locally at call time (`from ..agent.skills import
    # validate_skill`), so patching the real module attribute here is what actually takes effect -
    # this simulates a slug that failed conformance without needing to hand-craft one.
    monkeypatch.setattr(
        skills_mod,
        "validate_skill",
        lambda sk: {"errors": ["Name: not conformant"], "warnings": []},
    )
    c, app_mod2 = _client(tmp_path)
    try:
        r = c.post(
            "/skills/create",
            data={"scope": "personal", "name": "Whatever", "instructions": "do it"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "error=invalid_skill" in r.headers["location"]
        from anthill.wiki.workspace import workspace_for

        s = app_mod2._SessionFactory()
        u = s.query(User).first()
        ws = workspace_for("personal", user_id=u.id)
        assert not (ws.skills / "whatever" / "SKILL.md").exists()
    finally:
        app_mod2._engine = None
        app_mod2._SessionFactory = None
