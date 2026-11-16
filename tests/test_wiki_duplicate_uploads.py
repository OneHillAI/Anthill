"""An upload that duplicates or collides with a page already in the wiki is never written (#149).
The same file uploaded again used to make a second page under a different model-chosen title, and
two different files with the same title silently overwrote each other. Now the new page is held in
the review queue with a proposal from Anthill, the banner says so, and nothing changes until the
person keeps the existing page, replaces it, or keeps both. Model-free: a fake backend."""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, User, WikiReview
from anthill.wiki.workspace import workspace_for

NOTE = b"# Bakery interview\n\nThe owner bakes at 4am.\n"


class _Backend:
    model = "test-model"

    def __init__(self):
        self.reply = "# Orders App Redesign\n\nThe owner bakes at 4am.\n"

    def chat(self, messages, **kw):
        if "review a proposed change" in messages[0].content:  # the org review gate
            return '{"summary": "ok", "flags": []}'
        return self.reply


def _setup(tmp_path, monkeypatch, role="member"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

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
    user = User(org_id=org.id, email="u@acme.com", role=role, active=True)
    s.add(user)
    s.commit()
    backend = _Backend()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, role))
    return client, app_mod, backend, user.id, org.id


def _upload(client, backend, name, data, reply=None, scope="personal"):
    if reply is not None:
        backend.reply = reply
    r = client.post(
        "/wiki/upload",
        data={"target_scope": scope},
        files={"file": (name, data, "text/markdown")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    return r.headers["location"]


def _pending(app_mod, org_id):
    return app_mod._SessionFactory().query(WikiReview).filter_by(org_id=org_id).all()


def _page_files(ws):
    return sorted(p.name for p in ws.pages())


def test_a_brand_new_upload_still_publishes(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    assert "saved=added" in _upload(client, backend, "bakery.md", NOTE)
    assert "saved=added" in _upload(client, backend, "other.md", b"# Courier\n\nOther.\n")
    assert len(ws.pages()) == 2
    assert _pending(app_mod, org_id) == []


def test_the_same_file_again_is_held_not_written(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    _upload(client, backend, "bakery.md", NOTE)
    before = {p.name: p.read_text() for p in ws.pages()}

    loc = _upload(client, backend, "bakery.md", NOTE)

    assert "saved=held" in loc and "saved=added" not in loc
    assert {p.name: p.read_text() for p in ws.pages()} == before  # one page, byte-identical
    (rev,) = _pending(app_mod, org_id)
    assert rev.status == "pending" and rev.target_scope == "personal"
    assert "same_file" in json.loads(rev.flags)
    assert "same file as 'Bakery interview'" in rev.outline
    assert "Recommended: keep the existing page" in rev.outline
    assert rev.slug == "bakery-interview"  # the page a replace would write over


def test_a_renamed_copy_of_the_same_file_is_still_the_same_file(tmp_path, monkeypatch):
    client, _, backend, uid, _ = _setup(tmp_path, monkeypatch)
    _upload(client, backend, "bakery.md", NOTE)
    assert "saved=held" in _upload(client, backend, "bakery (copy).md", NOTE)
    assert len(workspace_for("personal", user_id=uid).pages()) == 1


def test_the_same_file_with_a_different_model_title_is_held(tmp_path, monkeypatch):
    """The reported case: a note with no H1 got a new invented title on every run, so the second
    upload became a second page. The source hash catches it whatever the title."""
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    note = b"The owner bakes at 4am and hates the orders app.\n"
    _upload(client, backend, "bakery.txt", note, reply="# Orders App Redesign\n\nSummary.\n")
    loc = _upload(
        client, backend, "bakery.txt", note, reply="# Interview: Bakery Owner\n\nSummary.\n"
    )
    assert "saved=held" in loc
    assert _page_files(ws) == ["orders-app-redesign.md"]
    (rev,) = _pending(app_mod, org_id)
    assert rev.slug == "orders-app-redesign" and "# Interview: Bakery Owner" in rev.content


def test_a_different_file_with_the_same_title_does_not_overwrite(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    _upload(
        client,
        backend,
        "week-1.md",
        b"# Weekly notes\n\nalpha\n",
        reply="# Weekly notes\n\nalpha\n",
    )
    before = (ws.wiki / "weekly-notes.md").read_text()

    loc = _upload(
        client, backend, "week-2.md", b"# Weekly notes\n\nbeta\n", reply="# Weekly notes\n\nbeta\n"
    )

    assert "saved=held" in loc
    assert (ws.wiki / "weekly-notes.md").read_text() == before
    (rev,) = _pending(app_mod, org_id)
    assert "title_clash" in json.loads(rev.flags)
    assert "different file" in rev.outline and "Recommended: keep both" in rev.outline
    assert "beta" in rev.content


def test_an_edited_version_of_the_same_file_name_recommends_replacing(tmp_path, monkeypatch):
    client, app_mod, backend, _, org_id = _setup(tmp_path, monkeypatch)
    _upload(client, backend, "plan.md", b"# Plan\n\nv1\n", reply="# Plan\n\nv1\n")
    assert "saved=held" in _upload(
        client, backend, "plan.md", b"# Plan\n\nv2\n", reply="# Plan\n\nv2\n"
    )
    (rev,) = _pending(app_mod, org_id)
    assert "new_version" in json.loads(rev.flags)
    assert "Recommended: replace the existing page" in rev.outline


def test_the_banner_and_review_page_say_held_with_the_proposal(tmp_path, monkeypatch):
    client, _, backend, _, _ = _setup(tmp_path, monkeypatch)
    _upload(client, backend, "bakery.md", NOTE)
    loc = _upload(client, backend, "bakery.md", NOTE)

    page = client.get("/wiki" + loc[loc.index("?") :]).text
    assert "not</b> added" in page and "held for your decision" in page
    assert "added to this wiki" not in page

    review = client.get("/wiki/review/personal").text
    assert "Held upload" in review and "same file as" in review
    assert "Keep existing (recommended)" in review
    assert "Replace existing" in review and "Keep both" in review


def test_the_suggestions_inbox_lists_it_without_a_one_click_approve(tmp_path, monkeypatch):
    client, _, backend, _, _ = _setup(tmp_path, monkeypatch)
    _upload(client, backend, "bakery.md", NOTE)
    _upload(client, backend, "bakery.md", NOTE)
    body = client.get("/knowledge/suggestions").text
    assert "Held upload - possible duplicate" in body and "same file as" in body
    assert "/approve" not in body and "Decide" in body


def test_keep_existing_discards_the_new_page(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    _upload(client, backend, "bakery.md", NOTE)
    before = {p.name: p.read_text() for p in ws.pages()}
    _upload(client, backend, "bakery.md", NOTE)
    (rev,) = _pending(app_mod, org_id)

    r = client.post(f"/wiki/review/{rev.id}/reject", follow_redirects=False)

    assert r.status_code == 302
    assert {p.name: p.read_text() for p in ws.pages()} == before
    assert _pending(app_mod, org_id)[0].status == "rejected"


def test_replace_writes_the_new_version_over_the_existing_page(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    _upload(client, backend, "plan.md", b"# Plan\n\nv1\n", reply="# Plan\n\nv1\n")
    _upload(client, backend, "plan.md", b"# Plan\n\nv2\n", reply="# Plan\n\nv2\n")
    (rev,) = _pending(app_mod, org_id)

    r = client.post(f"/wiki/review/{rev.id}/approve", follow_redirects=False)

    assert r.status_code == 302
    assert _page_files(ws) == ["plan.md"]
    assert (
        "v2" in (ws.wiki / "plan.md").read_text() and "v1" not in (ws.wiki / "plan.md").read_text()
    )
    assert _pending(app_mod, org_id)[0].status == "approved"


def test_keep_both_adds_the_new_page_under_a_distinct_title(tmp_path, monkeypatch):
    client, app_mod, backend, uid, org_id = _setup(tmp_path, monkeypatch)
    ws = workspace_for("personal", user_id=uid)
    _upload(
        client,
        backend,
        "week-1.md",
        b"# Weekly notes\n\nalpha\n",
        reply="# Weekly notes\n\nalpha\n",
    )
    existing = (ws.wiki / "weekly-notes.md").read_text()
    _upload(
        client, backend, "week-2.md", b"# Weekly notes\n\nbeta\n", reply="# Weekly notes\n\nbeta\n"
    )
    (rev,) = _pending(app_mod, org_id)

    r = client.post(f"/wiki/review/{rev.id}/keep-both", follow_redirects=False)

    assert r.status_code == 302
    files = _page_files(ws)
    assert len(files) == 2 and "weekly-notes.md" in files
    assert (ws.wiki / "weekly-notes.md").read_text() == existing
    new = next(f for f in files if f != "weekly-notes.md")
    assert new.startswith("weekly-notes-uploaded-")
    text = (ws.wiki / new).read_text()
    assert "beta" in text and "# Weekly notes (uploaded " in text
    assert _pending(app_mod, org_id)[0].status == "approved"


def test_keep_both_refuses_a_review_that_is_not_a_held_upload(tmp_path, monkeypatch):
    client, app_mod, _, uid, org_id = _setup(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=org_id, proposed_by=uid, slug="x", content="# X\n", target_scope="personal",
        flags=json.dumps(["duplicate"]), status="pending",
    )  # fmt: skip
    s.add(rev)
    s.commit()
    assert (
        client.post(f"/wiki/review/{rev.id}/keep-both", follow_redirects=False).status_code == 400
    )


def test_an_org_upload_is_held_for_the_admin(tmp_path, monkeypatch):
    client, app_mod, backend, _, org_id = _setup(tmp_path, monkeypatch, role="admin")
    ws = workspace_for("org", org_id=org_id)
    assert "saved=added" in _upload(client, backend, "a.md", NOTE, scope="org")
    assert "saved=held" in _upload(client, backend, "a.md", NOTE, scope="org")
    assert len(ws.pages()) == 1
    (rev,) = _pending(app_mod, org_id)
    assert rev.target_scope == "org" and "same_file" in json.loads(rev.flags)
    assert "same file as" in client.get("/wiki/review").text


def test_a_queued_upload_of_a_file_already_in_the_wiki_is_held_and_the_user_told(
    tmp_path, monkeypatch
):
    """The first-run queue files uploads later through the scheduler; it must not bypass the check."""
    import anthill.web.app as app_mod
    from anthill.web import scheduler
    from anthill.web.db import Base, Notification, OrgSettings, QueuedUpload

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email="u@a.com", role="member", active=True)
    db.add_all([user, OrgSettings(org_id=org.id)])
    db.commit()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _Backend())
    ws = workspace_for("personal", user_id=user.id)

    for n in (1, 2):
        stored = tmp_path / f"stored{n}.md"
        stored.write_bytes(NOTE)
        db.add(
            QueuedUpload(
                org_id=org.id,
                user_id=user.id,
                target_scope="personal",
                filename="bakery.md",
                stored_path=str(stored),
            )
        )
        db.commit()
        scheduler._process_queued_uploads_tick(eng)

    assert len(ws.pages()) == 1
    (rev,) = db.query(WikiReview).all()
    assert "same_file" in json.loads(rev.flags)
    assert any("held for your decision" in n.body for n in db.query(Notification).all())
