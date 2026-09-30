"""Chat answers export to a downloadable document (no agent mode), and the answer prompt
no longer leaks the wiki's internal SCHEMA into user-facing answers.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.wiki import prompts

# ── prompt: no schema leak, no wiki meta-narration ──────────────────────────────


def test_answer_prompt_drops_schema_and_meta_narration():
    msgs = prompts.answer_question("(the wiki is empty)", "How are music charts made?")
    system = msgs[0].content
    whole = "".join(m.content for m in msgs)
    # The wiki SCHEMA (Layers / index.md / Ingest-Answer-Lint) must not be anywhere in the prompt.
    assert "SCHEMA" not in whole
    assert "index.md" not in whole and "log.md" not in whole
    # And the model is told not to narrate the wiki's structure/emptiness.
    assert "Never describe the wiki's internal structure" in system
    assert "How are music charts made?" in msgs[-1].content  # the question is restated last


# ── download route ───────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add(member)
    s.commit()
    return TestClient(app_mod.app), {"org": org.id, "member": member.id}


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_download_creates_and_serves_a_doc(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])  # plain member, no agent mode
    r = client.post(
        "/chat/download",
        data={"content": "# Music charts\n\nThey rank songs by sales and streams.", "format": "md"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("/files/") and body["url"].endswith(".md")
    # the file is downloadable via the authed /files route, with the answer content
    got = client.get(body["url"])
    assert got.status_code == 200 and "rank songs by sales" in got.text


def test_download_accepts_xlsx_for_a_table_answer(tmp_path, monkeypatch):
    # Added alongside the per-answer export button (chat.html): create()'s _xlsx already parses a
    # markdown table out of the content, which a lot of real chat answers already are.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    r = client.post(
        "/chat/download",
        data={"content": "| a | b |\n|---|---|\n| 1 | 2 |", "format": "xlsx"},
    )
    assert r.status_code == 200
    assert r.json()["url"].endswith(".xlsx")


def test_download_rejects_bad_format_and_empty(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    assert client.post("/chat/download", data={"content": "x", "format": "exe"}).status_code == 400
    assert client.post("/chat/download", data={"content": "   ", "format": "md"}).status_code == 400


def test_download_requires_login(tmp_path, monkeypatch):
    client, _ids = _app(tmp_path, monkeypatch)
    r = client.post("/chat/download", data={"content": "x", "format": "md"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
