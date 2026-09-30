"""The in-app help chat: a grounded conversation that also funnels feature requests + bug reports into
the contribution intake. /help/ask returns {answer, offer}; /help/file creates a ContributionProposal
(source=help). Model-free (the model call is stubbed)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


class _FakeBackend:
    def __init__(self, answer="fallback answer"):
        self._answer = answer

    def chat(self, msgs):
        return self._answer


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

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
    u = User(org_id=org.id, email="ada@acme.com", display_name="Ada", role="admin", active=True)
    s.add(u)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return app_mod, client, {"org": org.id, "u": u.id}


def _stub_backend(monkeypatch):
    monkeypatch.setattr("anthill.web.app._backend_from_cfg", lambda cfg: _FakeBackend())


# ── /help/ask: conversation + the file offer ────────────────────────────────────


def test_help_ask_offers_to_file_a_feature(tmp_path, monkeypatch):
    _app_mod, client, _ = _app(tmp_path, monkeypatch)
    _stub_backend(monkeypatch)
    monkeypatch.setattr(
        "anthill.common.jsonchat.json_chat",
        lambda backend, msgs: (
            '{"answer":"You can pin one chat today.","file_kind":"feature",'
            '"file_idea":"allow pinning more than one chat"}'
        ),
    )
    r = client.post("/help/ask", data={"question": "I wish I could pin several chats"})
    assert r.status_code == 200
    d = r.json()
    assert "pin one chat" in d["answer"]
    assert d["offer"] == {"kind": "feature", "idea": "allow pinning more than one chat"}


def test_help_ask_plain_question_has_no_offer(tmp_path, monkeypatch):
    _app_mod, client, _ = _app(tmp_path, monkeypatch)
    _stub_backend(monkeypatch)
    monkeypatch.setattr(
        "anthill.common.jsonchat.json_chat",
        lambda backend, msgs: (
            '{"answer":"Agent mode runs a worker toward a goal.","file_kind":"none","file_idea":""}'
        ),
    )
    d = client.post("/help/ask", data={"question": "what is agent mode?"}).json()
    assert "Agent mode" in d["answer"] and d["offer"] is None


def test_help_ask_fails_closed_to_plain_answer(tmp_path, monkeypatch):
    _app_mod, client, _ = _app(tmp_path, monkeypatch)
    _stub_backend(monkeypatch)  # backend.chat returns "fallback answer"

    def boom(backend, msgs):
        raise RuntimeError("no json")

    monkeypatch.setattr("anthill.common.jsonchat.json_chat", boom)
    d = client.post("/help/ask", data={"question": "how do I invite a teammate?"}).json()
    assert d["answer"] == "fallback answer" and d["offer"] is None  # plain answer, no offer


# ── /help/file: file the feature/bug as a contribution ──────────────────────────


def test_help_file_creates_help_source_proposal(tmp_path, monkeypatch):
    from anthill.web.db import AuditLog, ContributionProposal

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    _stub_backend(monkeypatch)
    monkeypatch.setattr(
        "anthill.contribute.distil_proposal",
        lambda idea, ref, backend: {
            "title": "Pin multiple chats",
            "kind": "feature",  # the agent's guess; the user-declared kind should win
            "spec": "## Problem\nOnly one chat can be pinned",
            "priority": "medium",
            "complete": True,
        },
    )
    r = client.post(
        "/help/file", data={"kind": "bug", "idea": "pinning a second chat unpins the first"}
    )
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["title"] == "Pin multiple chats" and d["kind"] == "bug"

    s = app_mod._SessionFactory()
    row = s.query(ContributionProposal).filter(ContributionProposal.org_id == ids["org"]).first()
    assert row.source == "help" and row.kind == "bug"  # declared kind wins over the agent's guess
    assert row.agent_drafted is True and row.proposer_handle == "Ada"
    assert s.query(AuditLog).filter(AuditLog.event == "contribution.submitted").count() == 1


def test_help_file_empty_idea_rejected(tmp_path, monkeypatch):
    from anthill.web.db import ContributionProposal

    app_mod, client, _ = _app(tmp_path, monkeypatch)
    r = client.post("/help/file", data={"kind": "feature", "idea": "  "})
    assert r.status_code == 400
    assert app_mod._SessionFactory().query(ContributionProposal).count() == 0
