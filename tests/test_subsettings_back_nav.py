"""Every org admin sub-page reached from the Manage-organisation hub shows a "back to the hub" link in
the topbar (founder feedback: "all the sub-settings have no back button to the previous screen"). Top-
level pages (dashboard, chat, personal Settings) show none.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def test_back_nav_for_maps_subpages_to_their_parent():
    from anthill.web.app import _back_nav_for

    for path in ("/users", "/teams", "/metrics", "/backend", "/settings/organization"):
        assert _back_nav_for(path) == ("/settings/org", "Manage organisation"), path
    assert _back_nav_for("/settings/organization/wiki")[0] == "/settings/org"  # nested too
    assert _back_nav_for("/settings") == ("/personalize", "Settings")  # advanced knobs -> Settings
    # not sub-pages: the hub itself and top-level pages get no back link
    for path in ("/settings/org", "/personalize", "/", "/chat", "/knowledge"):
        assert _back_nav_for(path) == ("", ""), path


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="Acme", slug="acme")
    s.add(o)
    s.flush()
    s.add_all(
        [User(org_id=o.id, email="a@a.com", role="admin", active=True), OrgSettings(org_id=o.id)]
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(1, o.id, "admin"))
    return c


def test_org_subpage_renders_a_back_link_to_the_hub(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    body = c.get("/users").text
    assert 'class="topbar-back"' in body and 'href="/settings/org"' in body
    assert "Manage organisation" in body


def test_top_level_page_has_no_back_link(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert 'class="topbar-back"' not in c.get("/").text  # dashboard is a top-level page
