"""The Thinking button knows whether the model of the chat can think (docs/specs/chat-thinking-toggle.md).

A model with no thinking mode (Gemma, Mistral ...) answers the same with Thinking on or off, so the button says
so instead of offering a choice that does nothing. The page asks ``/chat/<id>/thinking-support`` after it has
loaded. Model-free: Ollama's answer is faked.
"""

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.inference import ollama as ollama_mod
from anthill.inference.ollama import OllamaBackend
from anthill.web import db as db_mod
from anthill.web import plane_routing


def _client(tmp_path, monkeypatch, settings=True):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    if settings:
        session.add(OrgSettings(org_id=org.id))
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    other = User(org_id=org.id, email="o@acme.com", role="member", active=True)
    session.add_all([user, other])
    session.flush()
    mine = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    theirs = Conversation(org_id=org.id, user_id=other.id, plane="solo")
    session.add_all([mine, theirs])
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, mine.id, theirs.id


def _plane(backend="ollama", model="gemma3:4b"):
    return plane_routing.PlaneInference(
        plane="solo",
        backend=backend,
        base_url="http://127.0.0.1:11434",
        model=model,
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (False, {"known": True, "thinks": False, "model": "gemma3:4b"}),
        (True, {"known": True, "thinks": True, "model": "gemma3:4b"}),
        (None, {"known": False}),  # the engine could not say: no claim either way
    ],
)
def test_the_route_reports_what_the_engine_says_about_the_model(
    tmp_path, monkeypatch, reported, expected
):
    client, cid, _theirs = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(plane_routing, "plane_inference", lambda *a, **k: _plane())
    asked: list = []
    monkeypatch.setattr(
        OllamaBackend, "thinking_support", lambda self, model=None: asked.append(model) or reported
    )
    r = client.get(f"/chat/{cid}/thinking-support")
    assert r.status_code == 200 and r.json() == expected
    assert asked == ["gemma3:4b"]  # the model that answers this chat, not a guess


def test_a_cloud_or_organisation_model_is_never_probed_as_ollama(tmp_path, monkeypatch):
    client, cid, _theirs = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        plane_routing, "plane_inference", lambda *a, **k: _plane("openai", "gpt-oss-120b")
    )

    def boom(self, model=None):
        raise AssertionError("an OpenAI-compatible endpoint must not be asked as Ollama")

    monkeypatch.setattr(OllamaBackend, "thinking_support", boom)
    assert client.get(f"/chat/{cid}/thinking-support").json() == {"known": False}


def test_a_plane_with_no_backend_is_unknown_not_an_error(tmp_path, monkeypatch):
    client, cid, _theirs = _client(tmp_path, monkeypatch)

    def down(*a, **k):
        raise plane_routing.PlaneUnavailable("no organisation server")

    monkeypatch.setattr(plane_routing, "plane_inference", down)
    r = client.get(f"/chat/{cid}/thinking-support")
    assert r.status_code == 200 and r.json() == {"known": False}


def test_no_saved_settings_yet_is_unknown_not_an_error(tmp_path, monkeypatch):
    client, cid, _theirs = _client(tmp_path, monkeypatch, settings=False)  # a fresh install

    def boom(self, model=None):
        raise AssertionError("no model has been chosen, so there is nothing to ask the engine")

    monkeypatch.setattr(OllamaBackend, "thinking_support", boom)
    r = client.get(f"/chat/{cid}/thinking-support")
    assert r.status_code == 200 and r.json() == {"known": False}


def test_another_users_chat_is_not_answered(tmp_path, monkeypatch):
    client, _mine, theirs = _client(tmp_path, monkeypatch)
    assert client.get(f"/chat/{theirs}/thinking-support").status_code == 404
    assert client.get("/chat/99999/thinking-support").status_code == 404


def test_it_needs_a_signed_in_user(tmp_path, monkeypatch):
    client, cid, _theirs = _client(tmp_path, monkeypatch)
    client.cookies.clear()
    r = client.get(f"/chat/{cid}/thinking-support", follow_redirects=False)
    assert r.status_code != 200 and "known" not in r.text


def test_thinking_support_is_true_false_or_unknown_and_only_the_answers_are_remembered(
    monkeypatch,
):
    ollama_mod._CAPABILITIES_CACHE.clear()
    answers = {"thinker": ["completion", "thinking"], "plain": ["completion", "vision"]}

    def fake_post(url, json=None, timeout=None):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"capabilities": answers[json["model"]]}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    be = OllamaBackend("http://127.0.0.1:1", "plain")
    assert be.thinking_support("thinker") is True
    assert be.thinking_support() is False  # the backend's own model
    assert be.can_think("thinker") is True and be.can_think() is False  # unchanged yes/no view

    def down(*a, **k):
        raise httpx.ConnectError("x")

    monkeypatch.setattr(httpx, "post", down)
    assert be.thinking_support("later") is None and be.can_think("later") is False
    assert (be.base_url, "later") not in ollama_mod._CAPABILITIES_CACHE  # not remembered
    ollama_mod._CAPABILITIES_CACHE.clear()
