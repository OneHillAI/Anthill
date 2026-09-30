"""Cloud-fallback provider connect: save an encrypted provider key from Settings, flow it into
the HybridPolicy, and validate it via /settings/cloud/test. The provider validation is stubbed so
the route is tested without a real network call."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _admin_client(tmp_path):
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
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_save_cloud_key_encrypts_it(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    r = c.post(
        "/settings",
        data={
            "cloud_enabled": "true",
            "cloud_provider": "openrouter",
            "cloud_api_key": "sk-secret-1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_api_key_enc and "sk-secret-1" not in cfg.cloud_api_key_enc  # encrypted at rest


def test_blank_key_keeps_the_saved_one(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )
    saved = app_mod._SessionFactory().query(OrgSettings).first().cloud_api_key_enc
    # resubmit with a blank key -> the saved (encrypted) key is preserved, not wiped
    c.post("/settings", data={"cloud_enabled": "true", "cloud_api_key": ""}, follow_redirects=False)
    assert app_mod._SessionFactory().query(OrgSettings).first().cloud_api_key_enc == saved


def test_stored_key_flows_into_hybrid_policy(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    policy = app_mod._policy_from_cfg(cfg)
    assert (
        policy is not None and policy.api_key == "sk-secret-1"
    )  # decrypted, env no longer required


def test_cloud_test_route_sets_status(tmp_path, monkeypatch):
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings",
        data={"cloud_enabled": "true", "cloud_api_key": "sk-secret-1"},
        follow_redirects=False,
    )

    import anthill.hybrid.providers as providers

    monkeypatch.setattr(providers, "validate_provider", lambda *a, **k: (True, "Authenticated."))
    r = c.post("/settings/cloud/test", follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"]  # JSON now (toast feedback), not a redirect
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_status == "validated" and cfg.cloud_status_detail

    monkeypatch.setattr(providers, "validate_provider", lambda *a, **k: (False, "rejected the key"))
    c.post("/settings/cloud/test", follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.cloud_status == "error" and "rejected" in cfg.cloud_status_detail
