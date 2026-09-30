"""Chat -> contribution intake (ASDD public-surface P1): an explicit product suggestion in chat becomes
a ContributionProposal via the intake agent. The detector is deliberately narrow so a normal task is
never misrouted into a product suggestion. Model-free (the intake call is stubbed)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.agent import intent
from anthill.web import db as db_mod

# ── the detector: fires on real suggestions, never hijacks a task ───────────────

SUGGESTIONS = [
    "I wish Anthill could email me a summary when a task finishes",
    "Feature request: dark mode for the wiki",
    "feature suggestion - let me pin more than one chat",
    "It would be great if Anthill supported LinkedIn login",
    "I'd love it if Anthill had a mobile app",
    "Anthill should support exporting the wiki as PDF",
    "Anthill needs to let me reorder the sidebar",
    "can Anthill add a keyboard shortcut for new chat",
    "Bug report: the snippet editor loses focus on save",
]

NOT_SUGGESTIONS = [
    "make me a summary of this document",
    "add a column with the totals to this table",
    "create an agent that watches our competitor page",
    "what should I do about the failing test?",
    "schedule a weekly report every Monday",
    "can you add these three numbers for me",
    "write a poem about ants",
    "should I use Postgres or SQLite here?",
    "research the latest on RAG techniques",
]


def test_looks_like_suggestion_precision():
    for m in SUGGESTIONS:
        assert intent.looks_like_suggestion(m) is True, m
    for m in NOT_SUGGESTIONS:
        assert intent.looks_like_suggestion(m) is False, m


def test_parse_suggestion_strips_label_keeps_wish():
    assert intent.parse_suggestion("Feature request: dark mode") == "dark mode"
    assert intent.parse_suggestion("Bug report - editor loses focus") == "editor loses focus"
    # a wish form has no label to strip; it reads fine whole
    wish = "I wish Anthill could email me summaries"
    assert intent.parse_suggestion(wish) == wish


# ── the /chat/suggest route ─────────────────────────────────────────────────────


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


def test_chat_suggest_creates_chat_source_proposal(tmp_path, monkeypatch):
    from anthill.web.db import AuditLog, ContributionProposal

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.web.app._backend_from_cfg", lambda cfg: object())
    monkeypatch.setattr(
        "anthill.contribute.distil_proposal",
        lambda idea, ref, backend: {
            "title": "Email task summaries",
            "kind": "feature",
            "spec": "## Problem\nNo summary\n\n## Acceptance criteria\n- email sent",
            "priority": "high",
            "complete": True,
            "needs_detail": "",
        },
    )
    r = client.post(
        "/chat/suggest",
        data={"conv_id": "", "idea": "I wish Anthill could email me summaries"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["title"] == "Email task summaries"

    s = app_mod._SessionFactory()
    row = s.query(ContributionProposal).filter(ContributionProposal.org_id == ids["org"]).first()
    assert row.source == "chat" and row.kind == "feature" and row.status == "submitted"
    assert row.agent_drafted is True and row.proposer_handle == "Ada"
    assert row.idea == "I wish Anthill could email me summaries"  # the raw untrusted idea is kept
    assert s.query(AuditLog).filter(AuditLog.event == "contribution.submitted").count() == 1


def test_chat_suggest_empty_idea_is_rejected(tmp_path, monkeypatch):
    from anthill.web.db import ContributionProposal

    app_mod, client, _ = _app(tmp_path, monkeypatch)
    r = client.post("/chat/suggest", data={"idea": "   "})
    assert r.status_code == 400
    assert app_mod._SessionFactory().query(ContributionProposal).count() == 0
