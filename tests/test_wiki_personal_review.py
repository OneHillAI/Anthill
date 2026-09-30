"""Personal wiki uploads flagged for review are approvable (issue #428).

A personal/solo upload that trips a flag (personal data, duplicate, ...) creates a
WikiReview(target_scope='personal', status='pending'). Before this fix the only review UI filtered to
target_scope='org', so the row was stuck forever: never a page, never grounds a chat answer. Now the
personal wiki has its own Review queue (/wiki/review/personal), scoped to the user's own proposals, and
approving it publishes the page to their personal wiki. The dashboard surfaces the pending count too.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _app(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
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
    other = User(org_id=org.id, email="bo@acme.com", display_name="Bo", role="member", active=True)
    s.add(u)
    s.add(other)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(u.id, org.id, "admin"))
    return app_mod, client, {"org": org.id, "u": u.id, "other": other.id}


def _pending(app_mod, org_id, uid, *, slug="acme-doc", scope="personal", by=None):
    from anthill.web.db import WikiReview

    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=org_id,
        proposed_by=by if by is not None else uid,
        slug=slug,
        kind="page",
        content=f"# {slug}\n\nsome flagged content with a phone extension",
        target_scope=scope,
        status="pending",
        flags='["personal_data"]',
    )
    s.add(rev)
    s.commit()
    return rev.id


def test_personal_queue_lists_only_my_personal_pending(tmp_path, monkeypatch):
    app_mod, client, ids = _app(tmp_path, monkeypatch)
    mine = _pending(app_mod, ids["org"], ids["u"], slug="my-doc")
    _pending(app_mod, ids["org"], ids["u"], slug="org-doc", scope="org")  # org: must not appear
    _pending(app_mod, ids["org"], ids["u"], slug="bos-doc", by=ids["other"])  # someone else's

    r = client.get("/wiki/review/personal")
    assert r.status_code == 200
    assert "my-doc" in r.text
    assert "org-doc" not in r.text  # org queue is separate
    assert "bos-doc" not in r.text  # not my proposal
    assert str(mine)  # sanity


def test_approve_personal_publishes_page_and_clears_queue(tmp_path, monkeypatch):
    from anthill.web.db import WikiReview
    from anthill.wiki.workspace import workspace_for

    app_mod, client, ids = _app(tmp_path, monkeypatch)
    rid = _pending(app_mod, ids["org"], ids["u"], slug="deploys")

    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/wiki/review/personal"  # back to the personal queue, not org

    s = app_mod._SessionFactory()
    assert s.get(WikiReview, rid).status == "approved"
    assert app_mod._personal_review_q(s, ids["org"], ids["u"]).count() == 0
    # the page is now published in the user's personal wiki -> it can ground a chat answer
    ws = workspace_for("personal", user_id=ids["u"])
    assert (ws.wiki / "deploys.md").exists()


def test_cannot_approve_someone_elses_personal_review(tmp_path, monkeypatch):
    app_mod, client, ids = _app(tmp_path, monkeypatch)
    rid = _pending(app_mod, ids["org"], ids["u"], slug="bos-doc", by=ids["other"])
    # logged in as Ada (admin), but the review belongs to Bo -> personal scope authorizes only Bo
    r = client.post(f"/wiki/review/{rid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 403


def test_personal_wiki_shows_review_tab_only_when_pending(tmp_path, monkeypatch):
    app_mod, client, ids = _app(tmp_path, monkeypatch)
    # no pending -> no Review tab (keeps the personal wiki uncluttered)
    tabs = client.get("/wiki").text.split('class="settings-tabs"', 1)[1].split("</div>", 1)[0]
    assert "/wiki/review/personal" not in tabs
    # a flagged upload appears -> the Review tab shows up with a badge, giving a way to approve it
    _pending(app_mod, ids["org"], ids["u"], slug="flagged-doc")
    tabs = client.get("/wiki").text.split('class="settings-tabs"', 1)[1].split("</div>", 1)[0]
    assert "/wiki/review/personal" in tabs and "nav-badge" in tabs


def test_dashboard_surfaces_personal_pending(tmp_path, monkeypatch):
    app_mod, client, ids = _app(tmp_path, monkeypatch)
    _pending(app_mod, ids["org"], ids["u"], slug="waiting-doc")
    # solo topology so the model-picker redirect does not intercept
    s = app_mod._SessionFactory()
    from anthill.web.db import OrgSettings

    s.add(OrgSettings(org_id=ids["org"], deployment_topology="solo", local_model_chosen=True))
    s.commit()
    r = client.get("/")
    assert r.status_code == 200
    assert "/wiki/review/personal" in r.text  # the attention item links to the personal queue
