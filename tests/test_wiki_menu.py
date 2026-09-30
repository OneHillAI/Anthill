"""The Wiki menu is one menu with Pages / Principles / Review sub-tabs; the Pages list is clickable
(a single-page view route); Research-a-topic and Build-your-wiki are merged into one card."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, User


def _client(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "orgwiki"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin-skills"))
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role=role, active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, role))
    return c, app_mod, o.id, u.id


def test_wiki_has_pages_principles_review_subtabs(tmp_path, monkeypatch):
    c, _, _, _ = _client(tmp_path, monkeypatch)  # admin
    page = c.get("/wiki/org").text
    assert 'class="settings-tabs"' in page
    tabs = page.split('class="settings-tabs"', 1)[1].split("</div>", 1)[0]
    assert (
        ">Pages<" in tabs and ">Principles<" in tabs and ">Review<" in tabs
    )  # admin org gets Review
    # the review queue renders the same sub-tab bar (folded into the Wiki menu)
    assert 'class="settings-tabs"' in c.get("/wiki/review").text


def test_member_wiki_has_no_review_tab(tmp_path, monkeypatch):
    c, _, _, _ = _client(tmp_path, monkeypatch, role="member")
    tabs = c.get("/wiki").text.split('class="settings-tabs"', 1)[1].split("</div>", 1)[0]
    assert ">Pages<" in tabs and ">Principles<" in tabs
    assert ">Review<" not in tabs  # review is org-admin only


def test_research_is_one_merged_card(tmp_path, monkeypatch):
    c, _, _, _ = _client(tmp_path, monkeypatch)
    page = c.get("/wiki/org").text
    assert "Research topics into wiki pages" in page  # the merged card
    assert 'action="/research/batch"' in page  # posts to the batch route (1+ topics)
    assert "Build your wiki" not in page  # the separate card is gone


def test_pages_are_clickable_and_viewable(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, _, _, _ = _client(tmp_path, monkeypatch)
    ws = workspace_for("org")
    ws.init()
    ws.write_page("Returns Policy", "# Returns Policy\n\n30-day window for unused items.\n")
    page = c.get("/wiki/org").text
    assert "/wiki/page/returns-policy?scope=org" in page  # the Pages list links to the view
    view = c.get("/wiki/page/returns-policy?scope=org")
    assert view.status_code == 200
    assert "30-day window" in view.text and "Returns Policy" in view.text
    assert c.get("/wiki/page/does-not-exist?scope=org").status_code == 404
