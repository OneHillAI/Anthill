"""Every upload leaves a record the wiki page lists, and a failure says what happened and what to do next.
Model-free: the model is a fake backend, never Ollama."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.inference.base import BackendError
from anthill.inference.ollama import OllamaBackend
from anthill.multimodal.reader import FileContent
from anthill.web import db as db_mod
from anthill.web import scheduler
from anthill.web import upload_status as ups
from anthill.web.db import Organization, OrgSettings, UploadRecord, User
from anthill.wiki import ingest as ingest_mod
from anthill.wiki.workspace import workspace_for

# A short note is filed as it is with no model call (#181); these tests use short fixtures and need
# them read by the model, as a longer document would be. One test below opts back in to the short path.
pytestmark = pytest.mark.usefixtures("summarise_every_note")

PAGE = "# Quarterly report\n\nRevenue grew in March.\n"
NOTE = b"# Bakery interview\n\nThe owner bakes at 4am.\n"


class _Backend:
    """Answers every ingest call with ``reply``; the review check passes everything."""

    def __init__(self, reply=PAGE, *, model="qwen2.5:3b", error=None, stall=0.0, timeout=300.0):
        self.reply, self.model, self.error = reply, model, error
        self.stall, self.timeout = stall, timeout

    def chat(self, messages, **kw):
        if "review a proposed change" in messages[0].content:
            return '{"summary": "ok", "flags": []}'
        if self.error is not None:
            raise self.error
        if self.stall > self.timeout:
            raise httpx.ReadTimeout("no answer")
        return self.reply


def _setup(tmp_path, monkeypatch, backend=None, *, pulling=""):
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
    users = {
        "owner": User(org_id=org.id, email="o@acme.com", role="member", active=True),
        "mate": User(org_id=org.id, email="m@acme.com", role="member", active=True),
        "admin": User(org_id=org.id, email="a@acme.com", role="admin", active=True),
        "admin2": User(org_id=org.id, email="b@acme.com", role="admin", active=True),
    }
    s.add_all(users.values())
    s.add(OrgSettings(org_id=org.id, local_model_pulling=pulling))
    s.commit()
    ids = {k: (u.id, org.id, u.role) for k, u in users.items()}
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend or _Backend())
    return app_mod, eng, TestClient(app_mod.app), ids


def _login(client, who):
    from anthill.web.crypto import make_token

    uid, org_id, role = who
    client.cookies.set("session_token", make_token(uid, org_id, role))


def _upload(client, name="bakery.md", data=NOTE, scope="personal"):
    return client.post(
        "/wiki/upload",
        data={"target_scope": scope},
        files={"file": (name, data, "application/octet-stream")},
        follow_redirects=False,
    )


def _text_pdf(text="Revenue grew in March."):
    from reportlab.pdfgen.canvas import Canvas

    out = BytesIO()
    canvas = Canvas(out)
    canvas.drawString(72, 720, text)
    canvas.save()
    return out.getvalue()


def _scanned_pdf_bytes(tmp_path):
    from tests.test_pdf_ingest import _scanned_pdf

    path = tmp_path / "scan.pdf"
    _scanned_pdf(path)
    return path.read_bytes()


def _records(app_mod):
    return app_mod._SessionFactory().query(UploadRecord).order_by(UploadRecord.id).all()


def _list(html: str) -> str:
    return html.split('id="wiki-recent-uploads"', 1)[1].split("</ul>", 1)[0]


def test_a_text_pdf_is_recorded_as_added_and_still_listed_after_the_banner_is_gone(
    tmp_path, monkeypatch
):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])

    r = _upload(client, "report.pdf", _text_pdf())

    assert "saved=added" in r.headers["location"]
    (rec,) = _records(app_mod)
    assert (rec.status, rec.model, rec.slug, rec.title) == (
        "added",
        "qwen2.5:3b",
        "quarterly-report",
        "Quarterly report",
    )
    assert rec.finished_at is not None
    html = client.get("/wiki").text
    assert "indexed by" not in html
    shown = _list(html)
    assert "report.pdf" in shown and "Added." in shown and "read by qwen2.5:3b" in shown
    assert 'href="/wiki/page/quarterly-report?scope=personal"' in shown


def test_a_scanned_pdf_with_a_text_only_model_says_why_and_what_to_do(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])

    r = _upload(client, "scan.pdf", _scanned_pdf_bytes(tmp_path))

    assert r.headers["location"].endswith("/wiki?error=scan")
    banner = client.get(r.headers["location"]).text
    reason = (
        "That PDF is made of page images (a scan) and your current AI reads text only. Add an "
        "image-reading model in Settings &gt; Model, or upload a text version."
    )
    assert reason in banner
    assert 'href="/personalize#model"' in banner
    assert "reach your AI" not in banner
    (rec,) = _records(app_mod)
    assert (rec.status, rec.detail) == ("failed", "scan")
    assert "Couldn&#39;t read." in _list(client.get("/wiki").text)
    assert reason in _list(client.get("/wiki").text)


def test_a_real_connection_failure_still_says_the_ai_could_not_be_reached(tmp_path, monkeypatch):
    try:
        raise httpx.ConnectError("refused")
    except httpx.ConnectError as cause:
        down = BackendError("Can't reach Ollama at http://localhost:11434.")
        down.__cause__ = cause
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch, _Backend(error=down))
    _login(client, ids["owner"])

    r = _upload(client)

    assert r.headers["location"].endswith("/wiki?error=aiunreachable")
    assert (
        "Couldn&#39;t reach your AI to read that document" in client.get(r.headers["location"]).text
    )
    assert _records(app_mod)[0].detail == "aiunreachable"


def test_an_unhealthy_backend_counts_as_a_connection_failure_and_a_healthy_one_does_not():
    other = BackendError("Ollama returned an empty response.")

    assert (
        ups.classify_failure(other, "Ollama not reachable at http://x.", "a.md") == "aiunreachable"
    )
    assert ups.classify_failure(other, "", "a.md") == "ingest"
    assert ups.classify_failure(ValueError("bad file"), "Ollama not reachable", "a.md") == "ingest"
    assert ups.classify_failure(httpx.ReadError("cut off"), "", "a.md") == "aiunreachable"


def test_the_readers_own_image_messages_map_to_the_image_reasons(monkeypatch):
    def pdf():
        return FileContent(text="", mime_type="application/pdf", source_name="scan.pdf")

    def image():
        return FileContent(text="", mime_type="image/png", source_name="photo.png")

    def raised(fc, backend):
        with pytest.raises(BackendError) as caught:
            ingest_mod._vision_backend_config(fc, backend)
        return caught.value

    text_only = _Backend()
    remote = OllamaBackend("http://example.com:11434", "qwen2.5:3b")
    local = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    monkeypatch.setattr("anthill.routing.router.TaskRouter.pick", lambda self, *a, **k: "")

    assert ups.classify_failure(raised(pdf(), text_only), "", "scan.pdf") == "scan"
    assert ups.classify_failure(raised(image(), text_only), "", "photo.png") == "imagetext"
    assert ups.classify_failure(raised(pdf(), remote), "", "scan.pdf") == "scanremote"
    assert ups.classify_failure(raised(pdf(), local), "", "scan.pdf") == "scan"
    assert ups.classify_failure(raised(image(), local), "", "photo.png") == "imagetext"
    empty = BackendError("This scanned PDF contains no extractable page images.")
    assert ups.classify_failure(empty, "", "scan.pdf") == "scanempty"


def test_pdf_problems_say_what_to_click_the_real_limits_and_to_try_again(tmp_path, monkeypatch):
    _app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])

    confirm = client.get("/wiki?error=pdfconfirm").text
    limit = client.get("/wiki?error=pdflimit").text
    retry = client.get("/wiki?error=pdfretry").text

    assert "Tick the box &#34;Confirm larger PDF processing&#34; under the file box" in confirm
    assert "click Upload" in confirm
    assert "200 pages, 25 MB and 10 minutes of reading" in limit
    assert "Split it into smaller PDFs" in limit
    assert "Nothing is wrong with the file" in retry and "try again" in retry


def test_an_upload_that_needs_confirmation_is_recorded_with_the_instruction(tmp_path, monkeypatch):
    from anthill.multimodal.reader import PdfPreflight

    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    monkeypatch.setattr(
        "anthill.wiki.ingest.read_file",
        lambda path, **kw: FileContent(
            text="Long report.",
            mime_type="application/pdf",
            source_name=path.name,
            pdf_preflight=PdfPreflight(
                page_count=41, image_count=0, decoded_image_bytes=0, vision_required=False
            ),
        ),
    )

    r = _upload(client, "long.pdf", b"%PDF long")

    assert r.headers["location"].endswith("?error=pdfconfirm")
    assert _records(app_mod)[0].detail == "pdfconfirm"
    assert "click Upload" in _list(client.get("/wiki").text)


def test_a_confirmed_pdf_is_recorded_when_it_is_processed(tmp_path, monkeypatch):
    from anthill.multimodal.reader import PdfPreflight

    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    ws = workspace_for("personal", user_id=ids["owner"][0])
    pending = ws.inbox / "needs-confirmation"
    pending.mkdir(parents=True)
    (pending / "held.pdf").write_bytes(b"held PDF")
    monkeypatch.setattr(
        "anthill.wiki.ingest.read_file",
        lambda path, **kw: FileContent(
            text="Held report.",
            mime_type="application/pdf",
            source_name=path.name,
            pdf_preflight=PdfPreflight(
                page_count=41, image_count=0, decoded_image_bytes=0, vision_required=False
            ),
        ),
    )

    r = client.post(
        "/wiki/upload/confirm",
        data={"target_scope": "personal", "name": "held.pdf"},
        follow_redirects=False,
    )

    assert "saved=added" in r.headers["location"]
    (rec,) = _records(app_mod)
    assert (rec.filename, rec.status, rec.model) == ("held.pdf", "added", "qwen2.5:3b")


def test_a_connector_import_is_recorded(tmp_path, monkeypatch):
    from anthill.web import docsource
    from anthill.web.db import MCPServer

    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(
        MCPServer(org_id=ids["admin"][1], name="Files", catalog_id="filesystem", status="approved")
    )
    s.commit()
    sid = s.query(MCPServer).one().id
    monkeypatch.setattr(docsource, "read_document", lambda srv, ref: "The imported body text.")
    _login(client, ids["admin"])

    r = client.post(
        "/wiki/import-connector",
        data={"server_id": sid, "ref": "folder/plan.md", "target_scope": "personal"},
        follow_redirects=False,
    )

    assert "saved=added" in r.headers["location"]
    (rec,) = _records(app_mod)
    assert (rec.filename, rec.status, rec.scope) == ("plan.md", "added", "personal")


def test_a_held_duplicate_and_a_flagged_page_show_their_own_status(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    _upload(client)
    assert "saved=held" in _upload(client).headers["location"]

    held = _list(client.get("/wiki").text)

    assert "Held for your decision." in held
    assert "nothing changed" in held and 'href="/wiki/review/personal"' in held

    ws = workspace_for("personal", user_id=ids["mate"][0])
    ws.init()
    ws.write_page("Company Policy Handbook", "# Company Policy Handbook\n\nAlready here.\n")
    _login(client, ids["mate"])
    monkeypatch.setattr(
        app_mod,
        "_backend_from_cfg",
        lambda cfg: _Backend("# Company Policy Handbook v2\n\nA second note.\n"),
    )
    assert "saved=queued" in _upload(client, "policy.txt", b"Notes on policy.").headers["location"]

    flagged = _list(client.get("/wiki").text)

    assert "Waiting for your review." in flagged and "flagged it" in flagged


def test_a_queued_upload_becomes_added_when_the_model_is_ready(tmp_path, monkeypatch):
    app_mod, eng, client, ids = _setup(tmp_path, monkeypatch, pulling="qwen2.5:3b")
    _login(client, ids["owner"])

    assert "saved=queued_for_model" in _upload(client).headers["location"]

    waiting = _list(client.get("/wiki").text)
    assert "Waiting for the model to finish downloading." in waiting
    assert "you do not need to upload it again" in waiting
    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({"local_model_pulling": ""})
    s.commit()
    scheduler._process_queued_uploads_tick(eng)

    (rec,) = _records(app_mod)
    assert (rec.status, rec.model) == ("added", "qwen2.5:3b")
    done = _list(client.get("/wiki").text)
    assert "Added." in done and "Waiting for the model" not in done
    assert "read by qwen2.5:3b" in done


def test_a_queued_scan_that_cannot_be_read_says_why_once_the_model_is_ready(tmp_path, monkeypatch):
    app_mod, eng, client, ids = _setup(tmp_path, monkeypatch, pulling="qwen2.5:3b")
    _login(client, ids["owner"])
    _upload(client, "scan.pdf", _scanned_pdf_bytes(tmp_path))
    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({"local_model_pulling": ""})
    s.commit()

    scheduler._process_queued_uploads_tick(eng)

    (rec,) = _records(app_mod)
    assert (rec.status, rec.detail) == ("failed", "scan")
    assert "page images (a scan)" in _list(client.get("/wiki").text)


def test_a_reading_row_cut_off_long_ago_shows_as_interrupted(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    now = datetime.now(timezone.utc)
    s = app_mod._SessionFactory()
    uid, org_id, _role = ids["owner"]
    for name, age in (("fresh.pdf", timedelta(minutes=2)), ("stuck.pdf", ups.READING_STALE_AFTER)):
        s.add(
            UploadRecord(
                org_id=org_id,
                user_id=uid,
                scope="personal",
                filename=name,
                status="reading",
                created_at=now - age - timedelta(seconds=5),
            )
        )
    s.commit()

    shown = _list(client.get("/wiki").text)

    items = {n: next(i for i in shown.split("<li") if n in i) for n in ("stuck.pdf", "fresh.pdf")}
    stuck, fresh = items["stuck.pdf"], items["fresh.pdf"]
    assert "Interrupted - try again." in stuck and "upload the file again" in stuck
    assert "Reading." in fresh and "Interrupted" not in fresh


def test_a_timed_out_upload_points_at_the_existing_retry_and_ends_added_after_it(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ingest_mod, "INGEST_MODEL_TIMEOUT_SECONDS", 0.05)
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])

    assert "error=ingesttimeout" in _upload(client).headers["location"]

    page = client.get("/wiki").text
    assert "Timed out." in _list(page) and 'href="#wiki-upload-retries"' in page
    assert 'id="wiki-upload-retries"' in page
    row = app_mod._SessionFactory().query(db_mod.QueuedUpload).one()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())
    client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)

    (rec,) = _records(app_mod)
    assert rec.status == "added"
    assert "Timed out" not in _list(client.get("/wiki").text)


def test_choosing_a_timed_out_file_again_replaces_its_timed_out_line(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_mod, "INGEST_MODEL_TIMEOUT_SECONDS", 0.05)
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    _upload(client)
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())

    _upload(client)

    (rec,) = _records(app_mod)
    assert rec.status == "added"


def test_only_the_owner_sees_their_uploads_even_an_admin_in_the_same_org(tmp_path, monkeypatch):
    _app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    _upload(client, "mine.md")
    _login(client, ids["admin"])
    _upload(client, "board.md", b"# Board\n\nMinutes.\n", scope="org")

    _login(client, ids["mate"])
    assert "mine.md" not in client.get("/wiki").text
    _login(client, ids["admin2"])
    assert "board.md" not in client.get("/wiki/org").text
    _login(client, ids["admin"])
    assert "board.md" in client.get("/wiki/org").text
    assert "board.md" not in client.get("/wiki").text


def test_file_names_are_escaped(tmp_path, monkeypatch):
    _app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    name = "<img src=x onerror=alert(1)>.md"

    _upload(client, name)

    html = client.get("/wiki").text
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;.md" in _list(html)


def test_the_list_is_capped_and_newest_first(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    for n in range(12):
        s.add(
            UploadRecord(
                org_id=org_id, user_id=uid, scope="personal", filename=f"doc-{n}.md", status="added"
            )
        )
    s.commit()

    names = re.findall(r"<b>(doc-\d+\.md)</b>", _list(client.get("/wiki").text))

    assert names == [f"doc-{n}.md" for n in range(11, 1, -1)]


def test_a_failing_record_write_never_stops_the_upload(tmp_path, monkeypatch):
    _app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    monkeypatch.setattr(ups, "UploadRecord", lambda **kw: 1 / 0)

    r = _upload(client)

    assert "saved=added" in r.headers["location"]
    assert len(workspace_for("personal", user_id=ids["owner"][0]).pages()) == 1


def test_the_upload_status_bar_points_at_the_list():
    template = (Path(ups.__file__).parent / "templates" / "wiki.html").read_text()

    assert "Recent uploads" in template.split("function wikiUploadSubmit")[1].split("</script>")[0]
    assert 'id="wiki-upload-result"' in template and 'id="wiki-recent-uploads"' in template


def test_a_note_filed_as_written_says_so_without_naming_a_model():
    assert ups._model_text("added", "verbatim") == "filed as written"
    assert ups._model_text("added", "qwen2.5:3b") == "read by qwen2.5:3b"
    assert ups._model_text("added", "") == ""


def test_a_short_note_filed_as_written_is_listed_without_a_model_name(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_mod, "_VERBATIM_NOTE_MAX_CHARS", 1_500)  # undo the fixture
    _app_mod, _eng, client, ids = _setup(
        tmp_path, monkeypatch, _Backend(error=RuntimeError("no model"))
    )
    _login(client, ids["owner"])

    r = _upload(client, "bakery.md", NOTE)

    assert "saved=added" in r.headers["location"]  # the model, which would fail, was never called
    shown = _list(client.get("/wiki").text)
    assert "bakery.md" in shown and "filed as written" in shown and "read by" not in shown
