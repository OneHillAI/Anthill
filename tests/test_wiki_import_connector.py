"""Wiki import from a connected document service: the file-list JSON endpoint and the
import route (reads the doc over MCP, runs it through the same ingest-then-summarise pipeline
as an upload, files it through the review gate, scope-aware)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import MCPServer, Organization, User


class _FakeBackend:
    """Fixed reply for any chat call - covers both the ingest summary and the review-gate JSON.
    Mirrors tests/test_document_upload.py's fake so the import route's ingest step (which now
    calls the backend, unlike the old raw-text path) doesn't need a real model."""

    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self._reply


def _client(tmp_path, role="admin", monkeypatch=None):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    if monkeypatch is not None:
        # The import route now ingests into a real Workspace (`_wiki_ws`/`ws.init()`) before
        # `propose_wiki_write` is (optionally) mocked out - keep it inside tmp_path.
        monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
        monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role=role, active=True)
    s.add(u)
    # an approved document-source connector (filesystem provides_files in the catalog)
    srv = MCPServer(org_id=o.id, name="Files", catalog_id="filesystem", status="approved")
    s.add(srv)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, srv.id


def test_connector_files_endpoint_lists(tmp_path, monkeypatch):
    c, _app, _org, sid = _client(tmp_path)
    from anthill.web import docsource

    monkeypatch.setattr(
        docsource, "list_documents", lambda srv, q: [{"label": "Doc A", "ref": "a.md"}]
    )
    r = c.get(f"/wiki/connector-files?server_id={sid}&q=plan")
    assert r.status_code == 200
    assert r.json() == {"files": [{"label": "Doc A", "ref": "a.md"}]}


def test_connector_files_unknown_server(tmp_path):
    c, _app, _org, _sid = _client(tmp_path)
    r = c.get("/wiki/connector-files?server_id=99999")
    assert r.json()["files"] == []


def test_import_files_through_review_gate(tmp_path, monkeypatch):
    c, app_mod, _org, sid = _client(tmp_path, monkeypatch=monkeypatch)
    from anthill.web import docsource

    monkeypatch.setattr(docsource, "read_document", lambda srv, ref: "The imported body text.")
    backend = _FakeBackend("# Imported Doc\n\nSummary of: The imported body text.")
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)
    calls = []
    monkeypatch.setattr(app_mod, "propose_wiki_write", lambda db, **kw: calls.append(kw) or True)
    r = c.post(
        "/wiki/import-connector",
        data={"server_id": sid, "ref": "folder/plan.md", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "saved=added" in r.headers["location"]
    assert len(calls) == 1
    kw = calls[0]
    assert kw["target_scope"] == "personal"
    # The connector's raw text is no longer filed as-is (#683 phase 4): it goes through the
    # same ingest-then-summarise pipeline an upload uses, so the page content is the model's
    # summary of the source, not the source verbatim.
    assert kw["content"].lstrip().startswith("# Imported Doc")
    assert "Summary of: The imported body text." in kw["content"]
    assert kw["source"].startswith("Imported from")
    # And the raw connector text really was sent to the model - proving this went through
    # summarisation rather than being filed verbatim.
    assert backend.calls
    assert "The imported body text." in str(backend.calls[0])


def test_import_empty_document_errors(tmp_path, monkeypatch):
    c, app_mod, _org, sid = _client(tmp_path)
    from anthill.web import docsource

    monkeypatch.setattr(docsource, "read_document", lambda srv, ref: "")
    monkeypatch.setattr(app_mod, "propose_wiki_write", lambda db, **kw: True)
    r = c.post(
        "/wiki/import-connector",
        data={"server_id": sid, "ref": "x", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=empty_doc" in r.headers["location"]


def test_import_ingest_failure_redirects_with_generic_error(tmp_path, monkeypatch):
    """A failure inside the shared ingest pipeline (e.g. the model backend is unavailable)
    surfaces as the same generic ?error=ingest an upload failure uses, not a 500."""
    c, app_mod, _org, sid = _client(tmp_path, monkeypatch=monkeypatch)
    from anthill.web import docsource

    monkeypatch.setattr(docsource, "read_document", lambda srv, ref: "Some source text.")

    def _raising_backend(cfg):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(app_mod, "_backend_from_cfg", _raising_backend)
    r = c.post(
        "/wiki/import-connector",
        data={"server_id": sid, "ref": "doc.md", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=ingest" in r.headers["location"]


def test_import_unknown_connector_errors(tmp_path):
    c, _app, _org, _sid = _client(tmp_path)
    r = c.post(
        "/wiki/import-connector",
        data={"server_id": 99999, "ref": "x", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=connector" in r.headers["location"]
