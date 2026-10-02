"""The app had no version number visible anywhere in the UI - not Settings, not a footer, not a
Tauri About dialog (founder, 2026-10-01: "do i see a version of the app inside the app?" - no).
Settings' "This device" tab is the right home for it: genuinely device/install-local, same as
Appearance right above it.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


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
    o = Organization(name="Solo", slug="s")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo", ollama_model="qwen2.5:7b")])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c


def test_this_device_tab_shows_the_app_version(tmp_path, monkeypatch):
    from anthill import __version__

    c = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    device_panel = body.split('data-st="device"', 2)[2]
    assert f"Anthill {__version__}" in device_panel
