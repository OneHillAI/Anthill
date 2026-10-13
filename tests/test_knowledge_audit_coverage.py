"""Phase 6 of #683: fill the real (verified) knowledge-mutation audit gaps.

The spec (`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 5) claimed "today only
`wiki.rejected` and `memory.to_wiki` are logged" - checked against the real code and found
substantially wrong: most knowledge-mutation routes already call `audit.log(...)`. The real, narrower
gaps fixed here:

- `skill_proposal_reject()` logged nothing at all.
- `wiki_import_okgf()` (bulk OKGF import) logged nothing at all.
- `approve_review`/`reject_review` logged the same `wiki.approved`/`wiki.rejected` event regardless of
  `WikiReview.kind`, so approving a skill or a principles change through the review gate was
  indistinguishable in the audit log from approving an ordinary wiki page.
- None of the knowledge-mutation routes captured the request IP (no `request: Request` param, no use
  of the existing `_audit_request()` helper that `/logout` and the export routes already use).
"""

import io
import tarfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import AuditLog, Organization, ProposedSkill, User, WikiReview


def _app(tmp_path, monkeypatch):
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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, {"org": org.id, "admin": admin.id}


def _events(event):
    return app_mod._SessionFactory().query(AuditLog).filter(AuditLog.event == event).all()


def _tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _pending_review(ids, *, kind="page", slug="acme-doc", scope="org"):
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=ids["org"],
        proposed_by=ids["admin"],
        slug=slug,
        kind=kind,
        content="# X\n\nbody" if kind != "skill" else "# X\n\ninstructions",
        target_scope=scope,
        status="pending",
    )
    s.add(rev)
    s.commit()
    return rev.id


# ── gap 1: a rejected skill proposal now logs an event ──────────────────────────


def test_skill_proposal_reject_is_audited(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    p = ProposedSkill(
        org_id=ids["org"],
        created_by=ids["admin"],
        scope="personal",
        name="Track",
        instructions="do the thing",
        status="pending",
    )
    s.add(p)
    s.commit()
    pid = p.id

    assert _events("skill.proposal_rejected") == []  # nothing logged before the fix point
    r = client.post(f"/skills/proposed/{pid}/reject", follow_redirects=False)
    assert r.status_code == 302

    rows = _events("skill.proposal_rejected")
    assert len(rows) == 1
    assert rows[0].org_id == ids["org"] and rows[0].user_id == ids["admin"]
    assert f"id={pid}" in rows[0].detail
    assert rows[0].ip == "testclient"  # request IP now captured too


def test_rejecting_an_already_gone_proposal_logs_nothing(tmp_path, monkeypatch):
    """Matches the pre-existing no-op behavior for a missing/foreign proposal id - only a real state
    change is audited."""
    client, _ids = _app(tmp_path, monkeypatch)
    assert client.post("/skills/proposed/999999/reject", follow_redirects=False).status_code == 302
    assert _events("skill.proposal_rejected") == []


# ── gap 2: a bulk OKGF import now logs an event, with a page count ──────────────


def test_wiki_import_okgf_is_audited_with_count(tmp_path, monkeypatch):
    from anthill.wiki import okf

    client, ids = _app(tmp_path, monkeypatch)
    bundle = _tgz(
        {
            "a.md": okf.to_okf(okf.OkfPage(type="Concept", title="A", body="one")),
            "b.md": okf.to_okf(okf.OkfPage(type="Concept", title="B", body="two")),
        }
    )
    assert _events("wiki.import_okgf") == []
    r = client.post(
        "/wiki/import.okgf",
        files={"file": ("wiki.okgf.tgz", bundle, "application/gzip")},
        data={"scope": "org"},
    )
    assert r.status_code == 200 and r.json()["imported"] == 2

    rows = _events("wiki.import_okgf")
    assert len(rows) == 1
    assert rows[0].org_id == ids["org"] and rows[0].user_id == ids["admin"]
    assert "count=2" in rows[0].detail and "scope=org" in rows[0].detail
    assert rows[0].ip == "testclient"


# ── gap 3: approve/reject through review are kind-aware ─────────────────────────


def test_approve_skill_review_logs_skill_approved_not_wiki_approved(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    rid = _pending_review(ids, kind="skill", slug="my-skill")
    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302

    assert len(_events("skill.approved")) == 1
    assert _events("wiki.approved") == []  # NOT logged under the generic wiki event


def test_reject_skill_review_logs_skill_rejected_not_wiki_rejected(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    rid = _pending_review(ids, kind="skill", slug="my-other-skill")
    r = client.post(f"/wiki/review/{rid}/reject", follow_redirects=False)
    assert r.status_code == 302

    assert len(_events("skill.rejected")) == 1
    assert _events("wiki.rejected") == []


def test_approve_principles_review_logs_principles_approved(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    rid = _pending_review(ids, kind="principles", slug="principles")
    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302

    assert len(_events("principles.approved")) == 1
    assert _events("wiki.approved") == []


def test_approve_page_review_still_logs_wiki_approved(tmp_path, monkeypatch):
    """Regression: an ordinary wiki page (the default/most common kind) keeps its existing event
    name - only skill/principles reviews get a new, more specific name."""
    client, ids = _app(tmp_path, monkeypatch)
    rid = _pending_review(ids, kind="page", slug="ordinary-page")
    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302

    rows = _events("wiki.approved")
    assert len(rows) == 1 and "slug=ordinary-page" in rows[0].detail


# ── gap 4: request IP is now captured on knowledge-mutation routes ──────────────


def test_skill_deleted_audit_row_has_ip(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("org", org_id=ids["org"])
    ws.init()
    (ws.skills / "demo").mkdir(parents=True, exist_ok=True)
    (ws.skills / "demo" / "SKILL.md").write_text("# demo\n")

    r = client.post("/skills/demo/delete", data={"scope": "org"}, follow_redirects=False)
    assert r.status_code == 302
    rows = _events("skill.deleted")
    assert len(rows) == 1
    assert rows[0].ip == "testclient"  # previously NULL - no `request: Request` param existed


def test_snippet_edit_audit_row_has_ip(tmp_path, monkeypatch):
    from anthill.web.db import Snippet

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    snip = Snippet(
        org_id=ids["org"], user_id=ids["admin"], content="old", tags="t", scope="personal"
    )
    s.add(snip)
    s.commit()
    sid = snip.id

    r = client.post(
        f"/snippets/{sid}/edit", data={"content": "new", "tags": "t"}, follow_redirects=False
    )
    assert r.status_code == 302
    rows = _events("snippet.edit")
    assert len(rows) == 1 and rows[0].ip == "testclient"
