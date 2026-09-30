"""Onboarding v1: the 'make it yours' personalization flow + the post-signup tour
state. The tour UI itself is client-side (static/tour.js); here we cover the server:
profile storage, persona injection into answers, and the onboarding flags.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MemoryItem, Organization, User


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
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
    return c, app_mod, user.id, org.id


def test_personalize_saves_profile_and_memory(tmp_path, monkeypatch):
    import anthill.memory as mem

    monkeypatch.setattr(mem, "embed_text", lambda t: None)  # no embedder in tests
    c, app_mod, uid, _ = _client(tmp_path)
    try:
        r = c.post(
            "/personalize",
            data={
                "role": "SRE",
                "context": "fintech",
                "tone": "terse",
                "format": "bullets",
                "goals": "reduce on-call",
                "donts": "no paid SaaS",
                "avoid": "history",
                "profile": "",  # blank -> server composes from the fields
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        s = app_mod._SessionFactory()
        u = s.get(User, uid)
        assert "SRE" in u.profile and u.onboarding_done is True
        mems = (
            s.query(MemoryItem)
            .filter(MemoryItem.user_id == uid, MemoryItem.source == "profile")
            .all()
        )
        assert any("SRE" in m.text for m in mems)  # mirrored into durable memory
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_onboarding_status_and_done(tmp_path):
    c, app_mod, _, _ = _client(tmp_path)
    try:
        assert c.get("/onboarding/status").json()["done"] is False
        assert c.post("/onboarding/done").json()["ok"] is True
        assert c.get("/onboarding/status").json()["done"] is True
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_ask_injects_profile_into_answer(tmp_path, monkeypatch):
    import numpy as np

    import anthill.wiki.ask as ask_mod
    from anthill.wiki.workspace import Workspace

    monkeypatch.setattr(ask_mod.emb, "embed", lambda *a, **k: np.zeros(4, dtype="float32"))

    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)

    captured = {}

    class _Backend:
        def chat(self, messages, **k):
            captured["text"] = "\n".join(getattr(m, "content", "") for m in messages)
            return "ok"

    ws = Workspace(tmp_path / "w")
    ws.init()
    ans, _slugs, _hit = ask_mod.ask(
        ws, "what is our deploy process?", _Backend(), profile="Role: SRE. Tone: terse."
    )
    assert ans == "ok"
    assert "Role: SRE" in captured["text"]  # the persona reached the model prompt
