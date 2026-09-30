"""#683 requirement 2: "Turn this conversation into a skill" - the same one-gesture capture pattern
as the snippet "Save to wiki" button, applied to skills. `POST /skills/draft-from-chat` builds a
transcript the same way `_distil_memory_from_chat` does (last ~8 messages, "{role}: {content}"
joined) and calls the SAME `draft_skill()` /skills/draft uses, returning the identical JSON shape."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import ChatMessage, Conversation, Organization, User


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
    other = User(org_id=org.id, email="other@acme.com", role="member", active=True)
    s.add_all([user, other])
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, title="Investor update plan")
    s.add(conv)
    s.flush()
    for i in range(10):  # more than the ~8-message window, oldest should be dropped
        s.add(
            ChatMessage(
                conversation_id=conv.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"turn {i}",
            )
        )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return c, app_mod, user.id, other.id, org.id, conv.id


class _FakeBackend:
    model = "test"

    def chat(self, messages, **kw):
        # echo back the transcript it was handed, so the test can assert on exactly what
        # skills_draft_from_chat built and sent to draft_skill().
        import json as _json

        user_msg = next((m.content for m in messages if m.role == "user"), "")
        return _json.dumps(
            {
                "name": "Investor update",
                "description": "d",
                "when_to_use": "w",
                "instructions": user_msg,
            }
        )


def test_draft_from_chat_returns_the_same_shape_as_draft(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    c, mod, _uid, _other_id, _org_id, conv_id = _client(tmp_path)
    try:
        monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend())
        r = c.post("/skills/draft-from-chat", data={"conv_id": conv_id})
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"name", "description", "when_to_use", "instructions"}
        assert body["name"] == "Investor update"
        # only the last 8 of the 10 seeded messages are windowed in (oldest 2 dropped), each as
        # "{role}: {content}" - matching _distil_memory_from_chat's exact transcript shape.
        assert "user: turn 2" in body["instructions"]
        assert "assistant: turn 9" in body["instructions"]
        for i in range(2, 10):
            assert f"turn {i}" in body["instructions"]
        assert "turn 0" not in body["instructions"] and "turn 1" not in body["instructions"]
    finally:
        mod._engine = None
        mod._SessionFactory = None


def test_draft_from_chat_falls_back_gracefully_when_drafting_fails(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    class _Boom:
        model = "x"

        def chat(self, *a, **k):
            raise RuntimeError("model down")

    c, mod, _uid, _other_id, _org_id, conv_id = _client(tmp_path)
    try:
        monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Boom())
        r = c.post("/skills/draft-from-chat", data={"conv_id": conv_id})
        assert r.status_code == 200
        body = r.json()
        assert body["instructions"]  # falls back to the raw transcript, never empty
        assert body["description"] == "" and body["when_to_use"] == ""
    finally:
        mod._engine = None
        mod._SessionFactory = None


def test_draft_from_chat_is_owner_scoped(tmp_path, monkeypatch):
    """Like snippet_to_wiki's IDOR guard: a user can't draft a skill from someone else's
    conversation by guessing its id."""
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    _c, mod, _uid, other_id, org_id, conv_id = _client(tmp_path)
    try:
        monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend())
        other_client = TestClient(mod.app)  # same app, a different user's session
        other_client.cookies.set("session_token", make_token(other_id, org_id, "member"))
        r = other_client.post("/skills/draft-from-chat", data={"conv_id": conv_id})
        assert r.status_code == 404
    finally:
        mod._engine = None
        mod._SessionFactory = None
