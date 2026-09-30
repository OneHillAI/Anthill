"""Document upload in the UI: a user uploads a file, the existing ingest pipeline summarises it
into a wiki page, and it's routed through the agent review gate (clean auto-applies, flagged queues).
This is the on-ramp to org knowledge without the CLI. The model backend is mocked (no Ollama), like
the other web-route tests.
"""

from io import BytesIO

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


class _FakeBackend:
    """Fixed reply for any chat call - covers both the ingest summary and the review-gate JSON.
    Empty flags => the page passes review and auto-applies."""

    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self._reply


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add_all([member, admin])
    s.commit()
    ids = {"org": org.id, "member": member.id, "admin": admin.id}

    # Clean review by default -> the page auto-applies.
    monkeypatch.setattr(
        app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend('{"summary":"ok","flags":[]}')
    )
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_upload_adds_page_to_personal_wiki(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={
            "file": ("onboarding.md", b"# Onboarding\n\nWe deploy on Fridays.\n", "text/markdown")
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "saved=added" in r.headers["location"]
    ws = workspace_for("personal", user_id=ids["member"])
    assert len(ws.pages()) == 1  # the uploaded doc became a wiki page


def test_flagged_upload_survives_the_request_boundary_and_can_be_approved(tmp_path, monkeypatch):
    """#827: a flagged upload redirected to "?saved=queued" (a success message) while the WikiReview
    row it should have created was silently discarded - propose_wiki_write() added it but never
    committed, and _DBSessionMiddleware only CLOSES a request's sessions, never commits them. Passed
    every prior test because those either construct a WikiReview directly with a committed session
    (test_wiki_personal_review.py), or use a fake backend that never flags anything at all (this
    file's default `_app()` fixture). This test does neither: it forces a REAL flag (a dangling
    [[link]], mechanical - the model review pass is skipped entirely for personal scope, so no backend
    JSON to fake) through the REAL /wiki/upload route, then checks from a FRESH session (a genuinely
    separate request/session, the same way a real page load would see it) rather than the same session
    the route used - the only way to actually catch a missing commit instead of reading an uncommitted
    add back out of the same session's identity map."""
    from anthill.web.db import WikiReview
    from anthill.wiki.workspace import workspace_for

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    # The ingest summariser's reply becomes the page body verbatim for a single-chunk .txt upload
    # (personal scope skips the model review pass entirely), so the dangling link needs to be IN the
    # fake reply, not the uploaded bytes - the upload's own text is what gets summarised, not what
    # becomes the page.
    monkeypatch.setattr(
        app_mod,
        "_backend_from_cfg",
        lambda cfg: _FakeBackend(
            "# Note\n\nSee [[a-page-that-does-not-exist]] for the full policy.\n"
        ),
    )

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("note.txt", b"See the linked page for the full policy.", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "saved=queued" in r.headers["location"]  # the route itself already claimed success

    ws = workspace_for("personal", user_id=ids["member"])
    assert len(ws.pages()) == 0  # nothing applied yet - it's supposed to be pending review

    fresh = app_mod._SessionFactory()  # a NEW session - must not see an uncommitted add
    review = fresh.query(WikiReview).filter_by(org_id=ids["org"]).one()
    assert review.status == "pending"
    assert "broken_links" in review.flags
    assert "a-page-that-does-not-exist" in review.content

    # Close the loop per QA's suggested regression: a second, independent request can find and act on
    # the row an earlier request created - proving it is durably committed, not just visible because
    # the test happens to reuse a session.
    r2 = client.post(f"/wiki/review/{review.id}/approve", data={}, follow_redirects=False)
    assert r2.status_code in (200, 302)
    assert len(workspace_for("personal", user_id=ids["member"]).pages()) == 1  # now it applies

    fresh2 = app_mod._SessionFactory()
    assert fresh2.query(WikiReview).filter_by(id=review.id).one().status == "approved"


def test_pdf_uploads_with_duplicate_names_preserve_both_raw_sources(tmp_path, monkeypatch):
    from reportlab.pdfgen.canvas import Canvas

    from anthill.wiki.workspace import workspace_for

    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    def pdf_bytes(text):
        output = BytesIO()
        canvas = Canvas(output)
        canvas.drawString(72, 720, text)
        canvas.save()
        return output.getvalue()

    for text in ("First quarterly report", "Replacement quarterly report"):
        response = client.post(
            "/wiki/upload",
            data={"target_scope": "personal"},
            files={"file": ("report.pdf", pdf_bytes(text), "application/pdf")},
            follow_redirects=False,
        )
        assert response.status_code == 302

    workspace = workspace_for("personal", user_id=ids["member"])
    raw_sources = list(workspace.raw.glob("*.pdf"))
    assert len(raw_sources) == 2
    assert raw_sources[0].read_bytes() != raw_sources[1].read_bytes()


def test_oversize_pdf_is_preserved_without_parsing(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    monkeypatch.setattr(app_mod, "_PDF_WEB_MAX_SOURCE_BYTES", 3)

    response = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("too-large.pdf", b"PDF bytes", "application/pdf")},
        follow_redirects=False,
    )

    assert "error=toobig" in response.headers["location"]
    workspace = workspace_for("personal", user_id=ids["member"])
    raw = list(workspace.raw.glob("*.pdf"))
    assert len(raw) == 1
    assert raw[0].read_bytes() == b"PDF bytes"


def test_confirmed_scheduler_pdf_is_consumed_once(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.workspace import workspace_for

    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    workspace = workspace_for("personal", user_id=ids["member"])
    pending = workspace.inbox / "needs-confirmation"
    pending.mkdir(parents=True)
    (pending / "held.pdf").write_bytes(b"held PDF")
    monkeypatch.setattr(
        "anthill.wiki.ingest.read_file",
        lambda path, **kwargs: FileContent(
            text="Held report.",
            mime_type="application/pdf",
            source_name=path.name,
            pdf_preflight=PdfPreflight(
                page_count=41,
                image_count=0,
                decoded_image_bytes=0,
                vision_required=False,
            ),
        ),
    )

    response = client.post(
        "/wiki/upload/confirm",
        data={"target_scope": "personal", "name": "held.pdf"},
        follow_redirects=False,
    )

    assert "saved=added" in response.headers["location"]
    assert not (pending / "held.pdf").exists()
    replay = client.post(
        "/wiki/upload/confirm",
        data={"target_scope": "personal", "name": "held.pdf"},
        follow_redirects=False,
    )
    assert "pdfconfirm_missing" in replay.headers["location"]


def test_pdf_confirmation_rejects_path_traversal_and_cross_scope_access(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    workspace = workspace_for("personal", user_id=ids["member"])
    pending = workspace.inbox / "needs-confirmation"
    pending.mkdir(parents=True)
    (pending / "held.pdf").write_bytes(b"held PDF")

    traversal = client.post(
        "/wiki/upload/confirm",
        data={"target_scope": "personal", "name": "../held.pdf"},
        follow_redirects=False,
    )
    assert "pdfconfirm_missing" in traversal.headers["location"]
    assert (pending / "held.pdf").exists()

    cross_scope = client.post(
        "/wiki/upload/confirm",
        data={"target_scope": "org", "name": "held.pdf"},
        follow_redirects=False,
    )
    assert "error=forbidden" in cross_scope.headers["location"]
    assert (pending / "held.pdf").exists()


def test_upload_rejects_unsupported_type(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("malware.exe", b"MZ\x00\x00", "application/octet-stream")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=unsupported" in r.headers["location"]
    ws = workspace_for("personal", user_id=ids["member"])
    assert not ws.exists() or len(ws.pages()) == 0  # nothing ingested


def test_large_pdf_upload_requires_explicit_confirmation(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.workspace import workspace_for

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    backend = _FakeBackend('{"summary":"ok","flags":[]}')
    offloads = []

    from starlette.concurrency import run_in_threadpool as real_run_in_threadpool

    async def recording_run_in_threadpool(func, *args, **kwargs):
        offloads.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)
    monkeypatch.setattr(
        "starlette.concurrency.run_in_threadpool",
        recording_run_in_threadpool,
    )
    monkeypatch.setattr(
        "anthill.wiki.ingest.read_file",
        lambda path, **kwargs: FileContent(
            text="Structured report.",
            mime_type="application/pdf",
            pdf_preflight=PdfPreflight(
                page_count=41,
                image_count=0,
                decoded_image_bytes=0,
                vision_required=False,
            ),
        ),
    )

    response = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("large.pdf", b"validated PDF", "application/pdf")},
        follow_redirects=False,
    )

    assert "error=pdfconfirm" in response.headers["location"]
    assert backend.calls == []
    workspace = workspace_for("personal", user_id=ids["member"])
    assert len(list(workspace.raw.glob("*.pdf"))) == 1

    response = client.post(
        "/wiki/upload",
        data={"target_scope": "personal", "confirm_large_pdf": "true"},
        files={"file": ("large.pdf", b"validated PDF", "application/pdf")},
        follow_redirects=False,
    )

    assert "saved=added" in response.headers["location"]
    assert backend.calls
    assert len(list(workspace.raw.glob("*.pdf"))) == 1
    assert len(offloads) == 2


def test_upload_requires_auth(tmp_path, monkeypatch):
    client, _app_mod, _ids = _app(tmp_path, monkeypatch)
    # no session cookie set
    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("x.md", b"# x\n", "text/markdown")},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 401, 403)  # rejected / redirected to login


def _upload_failing_pdf(tmp_path, monkeypatch, exc):
    """Upload a PDF whose ingest raises `exc`, and return the redirect location."""
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend("unused"))

    def read_file_raising(path, **kwargs):
        raise exc

    monkeypatch.setattr("anthill.wiki.ingest.read_file", read_file_raising)
    response = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": ("report.pdf", b"validated PDF", "application/pdf")},
        follow_redirects=False,
    )
    return response.headers["location"]


def test_transient_pdf_failure_offers_a_retry_instead_of_blaming_the_file(tmp_path, monkeypatch):
    """A host that cannot establish the worker memory limit must not tell the user their
    perfectly valid PDF is too big."""
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    location = _upload_failing_pdf(
        tmp_path,
        monkeypatch,
        PdfSafetyLimitExceeded(
            "PDF parsing cannot establish the worker memory safety limit.", transient=True
        ),
    )

    assert "error=pdfretry" in location


def test_structural_pdf_failure_keeps_the_permanent_limit_response(tmp_path, monkeypatch):
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    location = _upload_failing_pdf(
        tmp_path,
        monkeypatch,
        PdfSafetyLimitExceeded("PDF has 500 pages; the hard safety limit is 200."),
    )

    assert "error=pdflimit" in location


def test_pdf_error_copy_distinguishes_a_retryable_host_failure(tmp_path, monkeypatch):
    """The two error codes must render distinct copy, and the retryable one must not
    blame the file's size."""
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    retry_page = client.get("/wiki?error=pdfretry").text
    limit_page = client.get("/wiki?error=pdflimit").text

    assert "try again" in retry_page.lower()
    assert "exceeds Anthill's hard processing safety limits" not in retry_page
    assert "exceeds Anthill's hard processing safety limits" in limit_page
