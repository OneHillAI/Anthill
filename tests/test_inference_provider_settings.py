"""#661 inference-provider work: the Berget quick-connect card and the org wiki-hosting reminder
banner on Settings > Organization."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _app(tmp_path, monkeypatch, *, topology="org", wiki_hosting="local"):
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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    cfg = OrgSettings(org_id=org.id, deployment_topology=topology, wiki_hosting=wiki_hosting)
    s.add_all([admin, cfg])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client


def test_berget_quick_connect_card_present(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    body = client.get("/settings/organization").text
    assert "Quick-connect: Berget AI" in body
    assert "api.berget.ai/v1" in body


def test_wiki_banner_shown_for_org_with_local_hosting(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, topology="org", wiki_hosting="local")
    body = client.get("/settings/organization").text
    assert "still hosted locally" in body


def test_wiki_banner_absent_once_org_has_vpc_hosting(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, topology="org", wiki_hosting="vpc")
    body = client.get("/settings/organization").text
    assert "still hosted locally" not in body


def test_wiki_banner_absent_for_solo(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, topology="solo", wiki_hosting="local")
    body = client.get("/settings/organization").text
    assert "still hosted locally" not in body
