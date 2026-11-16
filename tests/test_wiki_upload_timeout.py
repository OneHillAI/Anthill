"""A text upload whose model call stalls ends in a clear timed-out state, offers a retry with a longer
limit from the saved file, and the upload page shows honest status (#148). Model-free."""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.inference.ollama import OllamaBackend
from anthill.web import db as db_mod
from anthill.web.db import Organization, QueuedUpload, User
from anthill.wiki import ingest as ingest_mod
from anthill.wiki.ingest import (
    IngestTimedOut,
    PdfProcessingLimitExceeded,
    _model_kwargs,
    _ProcessingBudget,
    ingest_model_timeout,
)
from anthill.wiki.workspace import workspace_for

# A short note is filed as it is, with no model call (#181), so it could never stall: these tests
# use short fixtures and need them summarised, as a longer note would be.
pytestmark = pytest.mark.usefixtures("summarise_every_note")

NOTE = b"# Bakery interview\n\nThe owner bakes at 4am.\n"
PAGE = "# Bakery interview\n\nThe owner bakes at 4am.\n"
TEMPLATE = Path(ingest_mod.__file__).parents[1] / "web" / "templates" / "wiki.html"


class _Backend:
    model = "test-model"

    def __init__(self, *, stall: float = 0.0, timeout: float = 300.0):
        self.stall = stall
        self.timeout = timeout
        self.timeouts_seen: list[float] = []

    def chat(self, messages, **kw):
        if "review a proposed change" in messages[0].content:
            return '{"summary": "ok", "flags": []}'
        self.timeouts_seen.append(self.timeout)
        if self.stall > self.timeout:
            time.sleep(self.timeout)
            raise httpx.ReadTimeout("no answer")
        return PAGE


def _setup(tmp_path, monkeypatch, backend):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

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
    other_org = Organization(name="Other", slug="other")
    s.add_all([org, other_org])
    s.flush()
    users = {
        "owner": User(org_id=org.id, email="o@acme.com", role="member", active=True),
        "mate": User(org_id=org.id, email="m@acme.com", role="member", active=True),
        "stranger": User(org_id=other_org.id, email="s@other.com", role="member", active=True),
    }
    s.add_all(users.values())
    s.commit()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)
    ids = {k: (u.id, u.org_id) for k, u in users.items()}
    return app_mod, TestClient(app_mod.app), ids


def _login(client, who):
    from anthill.web.crypto import make_token

    uid, org_id = who
    client.cookies.set("session_token", make_token(uid, org_id, "member"))


def _upload(client, name="bakery.md", data=NOTE):
    return client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": (name, data, "text/markdown")},
        follow_redirects=False,
    )


def _rows(app_mod):
    return app_mod._SessionFactory().query(QueuedUpload).all()


@pytest.fixture
def tiny_limits(monkeypatch):
    monkeypatch.setattr(ingest_mod, "INGEST_MODEL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(ingest_mod, "INGEST_EXTENDED_TIMEOUT_SECONDS", 7.0)


def test_a_stalled_text_upload_ends_timed_out_within_the_limit_and_writes_nothing(
    tmp_path, monkeypatch, tiny_limits
):
    backend = _Backend(stall=30.0)
    app_mod, client, ids = _setup(tmp_path, monkeypatch, backend)
    _login(client, ids["owner"])

    started = time.monotonic()
    r = _upload(client)

    assert time.monotonic() - started < 5
    assert r.status_code == 302 and r.headers["location"].endswith("/wiki?error=ingesttimeout")
    assert backend.timeouts_seen == [0.05]
    assert workspace_for("personal", user_id=ids["owner"][0]).pages() == []
    (row,) = _rows(app_mod)
    assert (row.status, row.filename, row.user_id) == ("timed_out", "bakery.md", ids["owner"][0])
    assert Path(row.stored_path).read_bytes() == NOTE


def test_the_page_says_what_happened_and_offers_the_longer_retry(
    tmp_path, monkeypatch, tiny_limits
):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    _upload(client)

    html = client.get("/wiki?error=ingesttimeout").text

    assert "did not finish reading that document within 0.05 seconds" in html
    assert "Try again with a longer limit" in html
    (row,) = _rows(app_mod)
    assert f'action="/wiki/upload/retry/{row.id}"' in html and "bakery.md" in html
    assert "7.0 seconds for each step" in html


def test_retry_runs_the_saved_file_with_the_longer_limit_and_no_new_selection(
    tmp_path, monkeypatch, tiny_limits
):
    slow = _Backend(stall=30.0)
    app_mod, client, ids = _setup(tmp_path, monkeypatch, slow)
    _login(client, ids["owner"])
    _upload(client)
    (row,) = _rows(app_mod)
    fast = _Backend()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: fast)

    r = client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)

    assert r.status_code == 302 and "saved=added" in r.headers["location"]
    assert fast.timeouts_seen == [7.0]
    assert fast.timeout == 300.0
    ws = workspace_for("personal", user_id=ids["owner"][0])
    assert [p.name for p in ws.pages()] == ["bakery-interview.md"]
    assert _rows(app_mod)[0].status == "done"
    assert "Try again with a longer limit" not in client.get("/wiki").text


def test_retry_goes_through_the_same_dedup_hold_as_an_upload(tmp_path, monkeypatch, tiny_limits):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend())
    _login(client, ids["owner"])
    assert "saved=added" in _upload(client).headers["location"]
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend(stall=30.0))
    assert "error=ingesttimeout" in _upload(client).headers["location"]
    (row,) = _rows(app_mod)
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())

    r = client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)

    assert "saved=held" in r.headers["location"]
    assert len(workspace_for("personal", user_id=ids["owner"][0]).pages()) == 1


def test_a_retry_that_times_out_again_says_so_and_keeps_the_button(
    tmp_path, monkeypatch, tiny_limits
):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    _upload(client)
    (row,) = _rows(app_mod)
    monkeypatch.setattr(ingest_mod, "INGEST_EXTENDED_TIMEOUT_SECONDS", 0.05)

    r = client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)

    assert r.headers["location"].endswith("?error=ingesttimeout_long")
    assert _rows(app_mod)[0].status == "timed_out"


def test_retry_refuses_someone_elses_upload_and_an_unknown_id(tmp_path, monkeypatch, tiny_limits):
    backend = _Backend(stall=30.0)
    app_mod, client, ids = _setup(tmp_path, monkeypatch, backend)
    _login(client, ids["owner"])
    _upload(client)
    (row,) = _rows(app_mod)
    backend.stall = 0.0
    calls = len(backend.timeouts_seen)

    for who in ("mate", "stranger"):
        _login(client, ids[who])
        r = client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)
        assert r.headers["location"].endswith("/wiki?error=retrymissing")
        assert f"/wiki/upload/retry/{row.id}" not in client.get("/wiki").text
    _login(client, ids["owner"])
    unknown = client.post("/wiki/upload/retry/9999", follow_redirects=False)

    assert unknown.headers["location"] == r.headers["location"]
    assert len(backend.timeouts_seen) == calls
    assert _rows(app_mod)[0].status == "timed_out"
    assert workspace_for("personal", user_id=ids["owner"][0]).pages() == []


def test_retry_refuses_a_stored_path_outside_the_wiki_raw_folder(
    tmp_path, monkeypatch, tiny_limits
):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    _upload(client)
    outside = tmp_path / "secret.md"
    outside.write_text("# Secret\n\nNot an upload.\n")
    s = app_mod._SessionFactory()
    row = s.query(QueuedUpload).one()
    row.stored_path = str(outside)
    s.commit()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())

    r = client.post(f"/wiki/upload/retry/{row.id}", follow_redirects=False)

    assert r.headers["location"].endswith("/wiki?error=retrymissing")
    assert workspace_for("personal", user_id=ids["owner"][0]).pages() == []


def test_choosing_the_file_again_and_succeeding_clears_the_retry_button(
    tmp_path, monkeypatch, tiny_limits
):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    _upload(client)
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())

    assert "saved=added" in _upload(client).headers["location"]

    assert _rows(app_mod)[0].status == "done"
    assert "Try again with a longer limit" not in client.get("/wiki").text


def test_a_different_file_with_the_same_name_keeps_its_retry_button(
    tmp_path, monkeypatch, tiny_limits
):
    app_mod, client, ids = _setup(tmp_path, monkeypatch, _Backend(stall=30.0))
    _login(client, ids["owner"])
    other = b"# Bakery interview\n\nA different file that happens to share the name.\n"
    _upload(client)  # both time out: two different files, one name
    _upload(client, data=other)
    assert len(_rows(app_mod)) == 2
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())

    assert "saved=added" in _upload(client).headers["location"]  # the first file goes through

    by_status = sorted(r.status for r in _rows(app_mod))
    assert by_status == ["done", "timed_out"]  # only the matching file loses its button


def test_the_default_ingest_limit_is_one_minute_and_the_longer_one_five():
    assert ingest_mod.INGEST_MODEL_TIMEOUT_SECONDS == 60
    assert ingest_mod.INGEST_EXTENDED_TIMEOUT_SECONDS == 300


def test_a_text_budget_caps_each_model_call_and_restores_the_backend_timeout():
    seen = []

    class Backend:
        timeout = 300.0

        def chat(self, messages, **kw):
            seen.append(self.timeout)
            return "ok"

    backend = Backend()
    with ingest_model_timeout(60):
        _ProcessingBudget.start(pdf=False).chat(backend, [])

    assert seen == [60] and backend.timeout == 300.0


def test_without_a_caller_limit_a_text_budget_leaves_the_backend_timeout_alone():
    seen = []

    class Backend:
        timeout = 300.0

        def chat(self, messages, **kw):
            seen.append(self.timeout)
            return "ok"

    _ProcessingBudget.start(pdf=False).chat(Backend(), [])

    assert seen == [300.0]


def test_a_pdf_budget_keeps_its_own_limit_whatever_the_text_limit_is():
    seen = []

    class Backend:
        timeout = 300.0

        def chat(self, messages, **kw):
            seen.append(self.timeout)
            raise httpx.ReadTimeout("slow")

    with ingest_model_timeout(60):
        budget = _ProcessingBudget.start(pdf=True)
        assert budget.model_timeout is None
        with pytest.raises(PdfProcessingLimitExceeded) as raised:
            budget.chat(Backend(), [])

    assert raised.value.transient and 60 < seen[0] <= 300.0


def test_a_text_timeout_becomes_ingest_timed_out_carrying_the_limit():
    class Backend:
        timeout = 300.0

        def chat(self, messages, **kw):
            raise httpx.ReadTimeout("slow")

    with ingest_model_timeout(60), pytest.raises(IngestTimedOut) as raised:
        _ProcessingBudget.start(pdf=False).chat(Backend(), [])

    assert raised.value.seconds == 60 and isinstance(raised.value, TimeoutError)


def test_text_calls_to_ollama_cap_the_generation_length_and_pdf_calls_do_not():
    ollama = OllamaBackend("http://localhost:11434", "qwen2.5:3b")

    assert _model_kwargs(ollama, None) == {"num_predict": 1_500, "think": False}
    assert _model_kwargs(ollama, "other") == {
        "num_predict": 1_500,
        "think": False,
        "model": "other",
    }
    assert _model_kwargs(ollama, "other", pdf=True) == {"model": "other"}
    assert _model_kwargs(object(), "other") == {}


def test_every_chunk_of_a_long_text_is_capped_not_just_the_document(tmp_path):
    calls = []

    class Spy(OllamaBackend):
        def chat(self, messages, **kw):
            calls.append(kw)
            return "# Long note\n\nA fact.\n"

    ws = workspace_for("personal", user_id=1)
    ws.init()
    source = tmp_path / "long.md"
    source.write_text("\n\n".join(f"Paragraph {i}. " + "word " * 1500 for i in range(6)))

    ingest_mod.ingest(
        ws, source, Spy("http://localhost:11434", "qwen2.5:3b"), on_write=lambda t, p: None
    )

    assert len(calls) > 2
    assert all(kw.get("num_predict") == 1_500 and kw.get("think") is False for kw in calls)


def test_the_upload_script_has_a_live_status_bar_that_never_uses_innerhtml():
    html = TEMPLATE.read_text()
    script = html[html.index("function wikiUploadSubmit") : html.index("DOMContentLoaded")]

    assert "textContent" in script and "innerHTML" not in script
    assert "upload.onprogress" in script and "Date.now()" in script
    assert "<progress" in html and 'role="status"' in html
    assert "up to a minute" not in html
    assert "Try again with a longer limit" in html
    assert re.search(r"xhr\.onerror", script) and "xhr.ontimeout" in script
    assert 'id="wiki-upload-result"' in html and "result.hidden = true" in script
