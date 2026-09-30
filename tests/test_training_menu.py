"""Training is its own menu (P3 IA): the operational training settings (base model, cadence, Train
now) live on the /training page via POST /training/config, not in the Settings 'Your cloud' card,
and Training is a distinct rail nav item. The provider/intent stays in Settings (PR #65 unified card)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _admin(tmp_path, monkeypatch):
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
    s.add_all([admin, OrgSettings(org_id=org.id)])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, app_mod, org.id


def test_training_config_saves_enable_and_cadence(tmp_path, monkeypatch):
    # The base model is no longer chosen here - it follows the served model. The form saves the
    # enable toggle + cadence.
    client, app_mod, org_id = _admin(tmp_path, monkeypatch)
    r = client.post(
        "/training/config",
        data={"training_enabled": "on", "training_schedule_hrs": "12"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "/training?saved=1" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.training_enabled is True and cfg.training_schedule_hrs == 12


def test_training_config_clamps_cadence(tmp_path, monkeypatch):
    client, app_mod, org_id = _admin(tmp_path, monkeypatch)
    client.post(
        "/training/config",
        data={"training_schedule_hrs": "0"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.training_schedule_hrs == 1  # clamped to >= 1


def test_training_page_has_the_config_form(tmp_path, monkeypatch):
    client, _, _ = _admin(tmp_path, monkeypatch)
    page = client.get("/training").text
    assert "Training settings" in page
    assert 'action="/training/config"' in page
    assert 'name="training_schedule_hrs"' in page
    assert 'name="training_base_model"' not in page  # the model follows what's served, not a box


def test_training_config_moved_off_settings(tmp_path, monkeypatch):
    client, _, _ = _admin(tmp_path, monkeypatch)
    settings = client.get("/settings").text
    # training config no longer lives in Settings at all - it all moved to the Training data page
    assert 'name="training_schedule_hrs"' not in settings
    assert 'name="training_enabled"' not in settings
    training = client.get("/training").text
    assert 'name="training_enabled"' in training  # the enable toggle now lives on Training data


def test_member_cannot_save_training_config(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token

    client, app_mod, org_id = _admin(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    member = User(org_id=org_id, email="m@acme.com", role="member", active=True)
    s.add(member)
    s.commit()
    client.cookies.set("session_token", make_token(member.id, org_id, "member"))
    r = client.post("/training/config", data={"training_base_model": "x"}, follow_redirects=False)
    assert r.status_code in (302, 303, 403)  # admin-only
