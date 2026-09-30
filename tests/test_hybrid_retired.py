"""The hybrid (paid-vendor) cloud fallback is retired from the UI but kept in the codebase.

The org plane is your own model on your own backend; the paid escalation-to-a-vendor path is no longer
offered to users (no Settings card, no Metrics tile). The code stays for now so nothing breaks and it can be
revisited; it is simply unreachable from the UI (and off by default).
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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    return TestClient(app_mod.app), {"org": org.id, "admin": admin.id}


def _auth(client, uid, org_id):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, "admin"))


def test_settings_no_longer_offers_the_cloud_fallback(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Hybrid cloud fallback" not in r.text
    assert 'name="cloud_enabled"' not in r.text  # no way to turn it on
    assert 'name="cloud_provider"' not in r.text


def test_metrics_no_longer_shows_cloud_escalations(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "Cloud escalations" not in r.text


def test_hybrid_code_is_kept():
    # the module is retained (dormant), just not reachable from the UI
    import anthill.hybrid

    assert hasattr(anthill.hybrid, "maybe_escalate")
