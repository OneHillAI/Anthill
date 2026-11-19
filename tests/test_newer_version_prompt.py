"""An upload that looks like a newer version of a page asks the person (docs/specs/newer-version-prompt.md).

A page whose title differs from an existing page only by numbers or version words ("Pricing 2025" and
"Pricing 2026") and whose text is mostly the same is held, nothing is written, and the review page asks "is this
a newer version?" with the changed words marked and three plain choices. Model-free: a fake backend."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import upload_dedup
from anthill.web.db import Organization, User, WikiReview
from anthill.wiki.workspace import Workspace, workspace_for

OLD = (
    b"# Pricing 2025\n\nStarter is 19 EUR per user per month. Team is 49 EUR per user per month.\n"
)
NEW = (
    b"# Pricing 2026\n\nStarter is 24 EUR per user per month. Team is 59 EUR per user per month. "
    b"Replaces the 2025 prices.\n"
)


class _Backend:
    model = "test-model"

    def chat(self, messages, **kw):
        return '{"summary": "ok", "flags": []}'


def _setup(tmp_path, monkeypatch):
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
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.commit()
    backend = _Backend()
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: backend)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, user.id, org.id


def _upload(client, name, data):
    r = client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": (name, data, "text/markdown")},
        follow_redirects=False,
    )
    assert r.status_code == 302


def _pending(app_mod, org_id):
    return app_mod._SessionFactory().query(WikiReview).filter_by(org_id=org_id).all()


def test_a_page_with_the_same_title_but_other_numbers_and_the_same_text_is_a_possible_newer_version():
    ws = Workspace(Path(tempfile.mkdtemp()))
    ws.init()
    ws.write_page("pricing-2025", OLD.decode())
    hit = upload_dedup.upload_conflict(ws, "pricing-2026", NEW.decode())
    assert hit and hit["flag"] == "looks_newer" and hit["slug"] == "pricing-2025"
    assert "looks like a newer version of 'Pricing 2025'" in hit["proposal"]


def test_different_documents_and_unrelated_titles_are_not_asked_about():
    ws = Workspace(Path(tempfile.mkdtemp()))
    ws.init()
    ws.write_page("roadmap-q3", "# Roadmap Q3\n\nLaunch the new onboarding in September.\n")
    q4 = "# Roadmap Q4\n\nPlan the migration to the new search index and retire the old cache.\n"
    assert upload_dedup.upload_conflict(ws, "roadmap-q4", q4) is None  # same stem, different text
    other = "# Hiring plan\n\nLaunch the new onboarding in September.\n"
    assert upload_dedup.upload_conflict(ws, "hiring-plan", other) is None  # same text, other title
    assert upload_dedup.upload_conflict(ws, "7", "# 7\n\nLaunch the new onboarding.\n") is None


def test_the_comparison_marks_only_the_words_that_changed():
    ws = Workspace(Path(tempfile.mkdtemp()))
    ws.init()
    ws.write_page("pricing-2025", OLD.decode())
    cmp = upload_dedup.compare_with_existing(ws, "pricing-2025", NEW.decode())
    assert cmp["title"] == "Pricing 2025" and len(cmp["rows"]) == 1
    old_changed = [t for changed, t in cmp["rows"][0]["old"] if changed]
    new_changed = [t for changed, t in cmp["rows"][0]["new"] if changed]
    assert "2025" in old_changed and "19" in old_changed and "49" in old_changed
    assert "2026" in new_changed and "24" in new_changed and "59" in new_changed
    assert upload_dedup.compare_with_existing(ws, "no-such-page", "x") is None


def test_the_review_page_asks_and_replace_writes_the_new_text_over_the_old_page(
    tmp_path, monkeypatch
):
    client, app_mod, uid, org_id = _setup(tmp_path, monkeypatch)
    _upload(client, "pricing-2025.md", OLD)
    _upload(client, "pricing-2026.md", NEW)
    (rev,) = _pending(app_mod, org_id)
    assert json.loads(rev.flags) == ["looks_newer"] and rev.slug == "pricing-2025"
    page = client.get("/wiki/review/personal").text
    assert "newer version?" in page  # the badge
    assert "would replace &ldquo;Pricing 2025&rdquo;" in page
    assert "<mark>2026</mark>" in page and "<mark>59</mark>" in page  # the changed words
    for text in ("Yes, replace", "No, keep both", "Keep the old page, discard this"):
        assert text in page, text
    assert page.index("Yes, replace") < page.index(
        "<textarea"
    )  # the choice comes before the text box
    r = client.post(
        f"/wiki/review/{rev.id}/approve", data={"content": rev.content}, follow_redirects=False
    )
    assert r.status_code in (302, 303)
    ws = workspace_for("personal", user_id=uid, org_id=org_id)
    assert [p.name for p in ws.pages()] == ["pricing-2025.md"]
    assert "Team is 59 EUR" in (ws.wiki / "pricing-2025.md").read_text()
    assert _pending(app_mod, org_id)[0].status != "pending"


def test_keep_both_files_the_new_page_under_its_own_title_without_a_date(tmp_path, monkeypatch):
    client, app_mod, uid, org_id = _setup(tmp_path, monkeypatch)
    _upload(client, "pricing-2025.md", OLD)
    _upload(client, "pricing-2026.md", NEW)
    (rev,) = _pending(app_mod, org_id)
    client.post(
        f"/wiki/review/{rev.id}/keep-both", data={"content": rev.content}, follow_redirects=False
    )
    ws = workspace_for("personal", user_id=uid, org_id=org_id)
    assert [p.name for p in ws.pages()] == ["pricing-2025.md", "pricing-2026.md"]


def test_the_inbox_and_upload_list_use_the_same_plain_words(tmp_path, monkeypatch):
    client, *_ = _setup(tmp_path, monkeypatch)
    _upload(client, "pricing-2025.md", OLD)
    _upload(client, "pricing-2026.md", NEW)
    inbox = client.get("/knowledge/suggestions").text
    assert "Held upload - may be a newer version" in inbox
    assert "Held upload - possible duplicate" not in inbox
    wiki = client.get("/wiki").text
    assert "a page this may be a newer version of" in wiki
