"""The /docs pages render. They read docs/*.md at runtime, so this guards that the files
exist, are wired to the routes, and render through the template (a missing file 500s - which
is exactly what happened when docs/ was not bundled into the packaged app).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

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
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add(member)
    s.commit()
    return TestClient(app_mod.app), {"org": org.id, "member": member.id}


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_how_it_works_renders(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    r = client.get("/docs/how-it-works")
    assert r.status_code == 200
    assert "How Anthill works" in r.text
    assert "in your perimeter" in r.text  # the reframed "in conjunction" content


def test_setup_guide_renders(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    r = client.get("/docs/setup")
    assert r.status_code == 200
    assert "Get started" in r.text
    # rewritten for an actual reader (solo or admin), not an admins-only playbook
    assert "Just for yourself" in r.text
    assert "Cloud &amp; model" in r.text
    assert "Invite your team" in r.text
