"""#683 requirement 3: the guided wizard's "does it trigger?" check. `POST /skills/check-trigger`
calls `match_skills()` - the SAME function `anthill/agent/executor.py` calls at real run time - against
a one-off draft skill, so it can never drift from actual matching behaviour (it is deliberately not a
separate, model-judged heuristic)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent.skills import Skill, match_skills
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


def test_relevant_prompt_triggers(tmp_path):
    c, app_mod = _client(tmp_path)
    try:
        r = c.post(
            "/skills/check-trigger",
            data={
                "description": "Draft the weekly investor update.",
                "when_to_use": "when asked to write the investor update",
                "instructions": "1. Pull metrics. 2. Write the summary.",
                "sample_prompt": "please write this week's investor update",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"would_trigger": True}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_unrelated_prompt_does_not_trigger(tmp_path):
    c, app_mod = _client(tmp_path)
    try:
        r = c.post(
            "/skills/check-trigger",
            data={
                "description": "Draft the weekly investor update.",
                "when_to_use": "when asked to write the investor update",
                "instructions": "1. Pull metrics. 2. Write the summary.",
                # deliberately avoids any word (including stopwords like "the") the matcher's cheap
                # keyword-overlap could accidentally collide on.
                "sample_prompt": "tell me a joke about pizza",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"would_trigger": False}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_empty_sample_prompt_never_triggers(tmp_path):
    c, app_mod = _client(tmp_path)
    try:
        r = c.post(
            "/skills/check-trigger",
            data={
                "description": "Draft the weekly investor update.",
                "when_to_use": "",
                "instructions": "do it",
                "sample_prompt": "",
            },
        )
        assert r.json() == {"would_trigger": False}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_route_result_matches_calling_match_skills_directly(tmp_path):
    """The route must not reimplement matching - it should produce exactly what match_skills() itself
    would say about the same draft and prompt (this is the whole point: no drift from run time)."""
    c, app_mod = _client(tmp_path)
    try:
        description, when_to_use, instructions = (
            "Summarize a PDF",
            "when handling a PDF",
            "extract text",
        )
        for prompt in ("please summarize this PDF for me", "what's the capital of France"):
            r = c.post(
                "/skills/check-trigger",
                data={
                    "description": description,
                    "when_to_use": when_to_use,
                    "instructions": instructions,
                    "sample_prompt": prompt,
                },
            )
            draft = Skill(
                slug="draft",
                name="draft",
                description=description,
                when_to_use=when_to_use,
                instructions=instructions,
            )
            expected = bool(match_skills([draft], prompt))
            assert r.json() == {"would_trigger": expected}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
