"""#278: the per-task "escalate on uncertainty" checkbox is only offered when there's actually a
connected backend to escalate to, persists through create/edit, and the org-wide monthly cap is
saveable and clamped on the admin Task defaults card."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch, *, org_backend=False):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, OrgSettings, User

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
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.flush()
    cfg = OrgSettings(org_id=org.id)
    if org_backend:
        cfg.org_backend_status = "validated"
        cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.add(cfg)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, org.id


def test_checkbox_hidden_when_nothing_connected(tmp_path, monkeypatch):
    client, _app_mod, _org_id = _app(tmp_path, monkeypatch, org_backend=False)
    body = client.get("/tasks").text
    assert 'name="escalate_on_uncertainty"' not in body


def test_checkbox_shown_when_a_backend_is_connected(tmp_path, monkeypatch):
    client, _app_mod, _org_id = _app(tmp_path, monkeypatch, org_backend=True)
    body = client.get("/tasks").text
    assert 'name="escalate_on_uncertainty"' in body


def test_create_task_persists_the_escalation_choice(tmp_path, monkeypatch):
    from anthill.web.db import ScheduledTask

    client, app_mod, _org_id = _app(tmp_path, monkeypatch, org_backend=True)
    client.post(
        "/tasks/create",
        data={
            "title": "digest",
            "goal": "summarize",
            "schedule": "once",
            "escalate_on_uncertainty": "true",
        },
    )
    t = app_mod._SessionFactory().query(ScheduledTask).order_by(ScheduledTask.id.desc()).first()
    assert t.escalate_on_uncertainty is True


def test_create_task_defaults_to_no_escalation(tmp_path, monkeypatch):
    from anthill.web.db import ScheduledTask

    client, app_mod, _org_id = _app(tmp_path, monkeypatch, org_backend=True)
    client.post("/tasks/create", data={"title": "digest", "goal": "summarize", "schedule": "once"})
    t = app_mod._SessionFactory().query(ScheduledTask).order_by(ScheduledTask.id.desc()).first()
    assert t.escalate_on_uncertainty is False


def test_edit_task_toggles_the_escalation_choice(tmp_path, monkeypatch):
    from anthill.web.db import ScheduledTask

    client, app_mod, _org_id = _app(tmp_path, monkeypatch, org_backend=True)
    client.post(
        "/tasks/create",
        data={
            "title": "digest",
            "goal": "summarize",
            "schedule": "once",
            "escalate_on_uncertainty": "true",
        },
    )
    t = app_mod._SessionFactory().query(ScheduledTask).order_by(ScheduledTask.id.desc()).first()
    client.post(
        f"/tasks/{t.id}/edit",
        data={"title": "digest", "goal": "summarize", "schedule": "once"},  # unchecked -> False
    )
    reloaded = app_mod._SessionFactory().query(ScheduledTask).filter_by(id=t.id).first()
    assert reloaded.escalate_on_uncertainty is False


def test_settings_save_clamps_the_monthly_cap(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    client, app_mod, org_id = _app(tmp_path, monkeypatch, org_backend=True)
    client.post(
        "/tasks/settings", data={"task_max_steps": "10", "task_escalation_cap_per_month": "9999"}
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.task_escalation_cap_per_month == 500  # clamped to the max


def test_settings_save_persists_a_valid_cap(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    client, app_mod, org_id = _app(tmp_path, monkeypatch, org_backend=True)
    client.post(
        "/tasks/settings", data={"task_max_steps": "10", "task_escalation_cap_per_month": "42"}
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.task_escalation_cap_per_month == 42
