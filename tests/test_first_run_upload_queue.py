"""First-run upload queue (#683 phase 5): while the local model is still downloading, an upload
is saved durably and queued instead of failing with a generic "model not ready" error, then
processed automatically by the scheduler once the model is ready.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Notification, Organization, OrgSettings, QueuedUpload, User


class _FakeBackend:
    """Fixed reply for any chat call - covers ingest summary + review-gate JSON, like the other
    web-route tests' fake backend."""

    def __init__(self, reply='{"summary":"ok","flags":[]}'):
        self._reply = reply
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self._reply


# ── wiki_upload(): queues instead of failing while the model is pulling ────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path / "files"))

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
    s.add(OrgSettings(org_id=org.id, local_model_pulling="qwen3.5:9b"))
    s.commit()
    ids = {"org": org.id, "member": member.id}
    return TestClient(app_mod.app), app_mod, ids, tmp_path


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_upload_while_model_pulling_creates_queued_row_not_a_page(tmp_path, monkeypatch):
    client, app_mod, ids, _root = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    def _fail_if_called(cfg):
        raise AssertionError("the model backend must not be invoked while it is still pulling")

    monkeypatch.setattr(app_mod, "_backend_from_cfg", _fail_if_called)

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={
            "file": ("onboarding.md", b"# Onboarding\n\nWe deploy on Fridays.\n", "text/markdown")
        },
        follow_redirects=False,
    )

    assert r.status_code == 302
    assert "saved=queued_for_model" in r.headers["location"]

    db = app_mod._SessionFactory()
    rows = db.query(QueuedUpload).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "queued"
    assert row.filename == "onboarding.md"
    assert row.org_id == ids["org"]
    assert row.user_id == ids["member"]
    assert row.target_scope == "personal"
    stored = Path(row.stored_path)
    assert stored.is_file()
    assert stored.read_bytes() == b"# Onboarding\n\nWe deploy on Fridays.\n"
    # nothing was ingested - no wiki page exists yet
    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("personal", user_id=ids["member"])
    assert not ws.exists() or len(ws.pages()) == 0
    db.close()


def test_upload_while_model_pulling_rejects_empty_file(tmp_path, monkeypatch):
    client, app_mod, ids, _root = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("empty.md", b"", "text/markdown")},
        follow_redirects=False,
    )
    assert "error=empty" in r.headers["location"]
    db = app_mod._SessionFactory()
    assert db.query(QueuedUpload).count() == 0
    db.close()


def test_upload_proceeds_normally_once_not_pulling(tmp_path, monkeypatch):
    """Once local_model_pulling clears, wiki_upload's early check is a no-op - the existing
    ingest-then-propose path runs exactly as before this phase."""
    client, app_mod, ids, _root = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    db = app_mod._SessionFactory()
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == ids["org"]).first()
    cfg.local_model_pulling = ""
    db.commit()
    db.close()

    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend())

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={
            "file": ("onboarding.md", b"# Onboarding\n\nWe deploy on Fridays.\n", "text/markdown")
        },
        follow_redirects=False,
    )
    assert "saved=added" in r.headers["location"]
    db = app_mod._SessionFactory()
    assert db.query(QueuedUpload).count() == 0  # never queued - it wasn't pulling
    db.close()


# ── scheduler._process_queued_uploads_tick(): drains the queue once the model is ready ─────────


def _seed_db(tmp_path, *, pulling: str = ""):
    from anthill.web.db import Base

    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    org = Organization(name="A", slug="a")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email="u@a.com", role="member", active=True)
    db.add_all([user, OrgSettings(org_id=org.id, local_model_pulling=pulling)])
    db.commit()
    ids = {"org": org.id, "user": user.id}
    db.close()
    return eng, ids


def _queue_upload(
    eng, ids, tmp_path, *, filename="doc.md", stored_filename=None, body=b"# Doc\n\nHello.\n"
):
    Session = sessionmaker(bind=eng)
    db = Session()
    stored = tmp_path / "stored" / (stored_filename or filename)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(body)
    row = QueuedUpload(
        org_id=ids["org"],
        user_id=ids["user"],
        target_scope="personal",
        filename=filename,
        stored_path=str(stored),
    )
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()
    return row_id, stored


def test_scheduler_processes_queued_upload_once_model_ready(tmp_path, monkeypatch):
    from anthill.web import scheduler

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))

    eng, ids = _seed_db(tmp_path, pulling="")  # already finished / never pulling
    row_id, stored = _queue_upload(eng, ids, tmp_path)

    import anthill.web.app as app_mod

    backend = _FakeBackend()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)

    scheduler._process_queued_uploads_tick(eng)

    Session = sessionmaker(bind=eng)
    db = Session()
    row = db.query(QueuedUpload).filter(QueuedUpload.id == row_id).first()
    assert row.status == "done"
    assert row.processed_at is not None
    assert not stored.exists()  # the durable copy is cleaned up once processed
    assert backend.calls  # the ingest pipeline actually ran (summarised, not filed verbatim)
    assert db.query(Notification).filter(Notification.user_id == ids["user"]).count() == 1
    note = db.query(Notification).filter(Notification.user_id == ids["user"]).first()
    assert "ready" in note.title.lower()
    db.close()

    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("personal", user_id=ids["user"])
    assert len(ws.pages()) == 1  # the queued doc became a real wiki page


def test_scheduler_uses_the_original_filename_for_the_fallback_title_not_the_stored_path(
    tmp_path, monkeypatch
):
    """Regression: the durable store gives the on-disk file a random collision-safe prefix
    (`_store_queued_upload`), but when the model's summary has no H1, ``ingest()`` falls back to
    the SOURCE FILE'S stem as the page title. Ingesting straight from the stored path leaked that
    random prefix into the wiki page's actual title/slug - found via live verification. Processing
    must use the clean original ``filename`` for that fallback, never the stored path's basename."""
    from anthill.web import scheduler

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))

    eng, ids = _seed_db(tmp_path, pulling="")
    row_id, _stored = _queue_upload(
        eng,
        ids,
        tmp_path,
        filename="vendor-contract.md",
        stored_filename="a1b2c3d4e5f6a7b8-vendor-contract.md",
        body=b"The vendor renews annually in March.\n",  # no leading '#': no H1 in the source
    )

    import anthill.web.app as app_mod

    # No '#' in the reply either, so ingest()'s own H1 fallback is exercised (source.stem).
    backend = _FakeBackend('{"summary":"ok","flags":[]}')
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)

    scheduler._process_queued_uploads_tick(eng)

    Session = sessionmaker(bind=eng)
    db = Session()
    row = db.query(QueuedUpload).filter(QueuedUpload.id == row_id).first()
    assert row.status == "done"
    db.close()

    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("personal", user_id=ids["user"])
    pages = ws.pages()
    assert len(pages) == 1
    assert "a1b2c3d4e5f6a7b8" not in pages[0].name
    text = pages[0].read_text()
    assert "a1b2c3d4e5f6a7b8" not in text
    assert "# vendor-contract" in text
    slug_path = ws.wiki / "vendor-contract.md"
    assert slug_path.is_file()  # slugified from the clean filename, not the stored path


def test_scheduler_leaves_queued_upload_untouched_while_still_pulling(tmp_path, monkeypatch):
    from anthill.web import scheduler

    eng, ids = _seed_db(tmp_path, pulling="qwen3.5:9b")  # still downloading
    row_id, stored = _queue_upload(eng, ids, tmp_path)

    import anthill.web.app as app_mod

    def _fail_if_called(cfg):
        raise AssertionError("must not ingest while the model is still pulling")

    monkeypatch.setattr(app_mod, "_backend_from_cfg", _fail_if_called)

    scheduler._process_queued_uploads_tick(eng)

    Session = sessionmaker(bind=eng)
    db = Session()
    row = db.query(QueuedUpload).filter(QueuedUpload.id == row_id).first()
    assert row.status == "queued"
    assert row.processed_at is None
    assert stored.exists()  # nothing touched it
    assert db.query(Notification).count() == 0
    db.close()


def test_scheduler_marks_error_and_notifies_when_the_stored_file_is_missing(tmp_path, monkeypatch):
    """A defensive path: if the durable file were ever lost (disk cleanup, manual deletion), the
    row is marked error and the user is told, instead of the tick silently looping forever."""
    from anthill.web import scheduler

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))

    eng, ids = _seed_db(tmp_path, pulling="")
    row_id, stored = _queue_upload(eng, ids, tmp_path)
    stored.unlink()  # simulate the file having disappeared

    import anthill.web.app as app_mod

    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend())

    scheduler._process_queued_uploads_tick(eng)

    Session = sessionmaker(bind=eng)
    db = Session()
    row = db.query(QueuedUpload).filter(QueuedUpload.id == row_id).first()
    assert row.status == "error"
    assert row.processed_at is not None
    assert row.error
    note = db.query(Notification).filter(Notification.user_id == ids["user"]).first()
    assert note is not None
    assert "could not" in note.title.lower()
    db.close()


def test_scheduler_only_processes_orgs_whose_model_is_no_longer_pulling(tmp_path, monkeypatch):
    """Two orgs, one still pulling and one ready: only the ready org's queued upload is drained."""
    from anthill.web.db import Base

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))

    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    ready_org = Organization(name="Ready", slug="ready")
    pulling_org = Organization(name="Pulling", slug="pulling")
    db.add_all([ready_org, pulling_org])
    db.flush()
    ready_user = User(org_id=ready_org.id, email="r@a.com", role="member", active=True)
    pulling_user = User(org_id=pulling_org.id, email="p@a.com", role="member", active=True)
    db.add_all(
        [
            ready_user,
            pulling_user,
            OrgSettings(org_id=ready_org.id, local_model_pulling=""),
            OrgSettings(org_id=pulling_org.id, local_model_pulling="qwen3.5:9b"),
        ]
    )
    db.commit()
    ready_ids = {"org": ready_org.id, "user": ready_user.id}
    pulling_ids = {"org": pulling_org.id, "user": pulling_user.id}
    db.close()

    ready_row_id, ready_stored = _queue_upload(eng, ready_ids, tmp_path, filename="ready.md")
    pulling_row_id, pulling_stored = _queue_upload(
        eng, pulling_ids, tmp_path, filename="pulling.md"
    )

    import anthill.web.app as app_mod
    from anthill.web import scheduler

    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend())

    scheduler._process_queued_uploads_tick(eng)

    db = Session()
    ready_row = db.query(QueuedUpload).filter(QueuedUpload.id == ready_row_id).first()
    pulling_row = db.query(QueuedUpload).filter(QueuedUpload.id == pulling_row_id).first()
    assert ready_row.status == "done"
    assert not ready_stored.exists()
    assert pulling_row.status == "queued"
    assert pulling_stored.exists()
    db.close()


# ── /local-model/status: reports queued-upload count for the dashboard banner ──────────────────


def test_local_model_status_reports_queued_upload_count(tmp_path, monkeypatch):
    client, app_mod, ids, _root = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    db = app_mod._SessionFactory()
    db.add(
        QueuedUpload(
            org_id=ids["org"],
            user_id=ids["member"],
            target_scope="personal",
            filename="a.md",
            stored_path=str(tmp_path / "a.md"),
        )
    )
    db.commit()
    db.close()

    r = client.get("/local-model/status")
    assert r.status_code == 200
    assert r.json()["queued_uploads"] == 1
