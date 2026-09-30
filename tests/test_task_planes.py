"""Plane-aware scheduled-task execution (P2 remainder): an Org task runs on the org backend; an Org
task with no backend fails (never silently runs on the local model). Tasks spawned from a chat
inherit that chat's plane."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Conversation, Organization, OrgSettings, ScheduledTask, User


def _engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def test_org_task_without_backend_fails_unavailable(tmp_path):
    from anthill.web import scheduler

    eng = _engine(tmp_path)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id))  # no org backend connected
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.add(
        ScheduledTask(
            org_id=org.id,
            title="org job",
            goal="do the thing",
            schedule="once",
            status="pending",
            next_run_at=past,
            plane="org",
        )
    )
    s.commit()

    scheduler._tick(eng)  # runs due tasks

    t = sessionmaker(bind=eng)().query(ScheduledTask).first()
    assert t.status == "failed"  # an Org task with no backend does not run locally
    assert "not connected" in (t.last_result or "")  # the PlaneUnavailable reason is recorded


# ── tasks spawned from chat inherit the conversation's plane ──────────────────────────


def _client(tmp_path, monkeypatch, *, org_backend=False):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = _engine(tmp_path)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.flush()
    cfg = OrgSettings(org_id=org.id)
    if org_backend:
        cfg.org_backend_status = "validated"
        cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.add(cfg)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, org.id, user.id


def _make_conv(app_mod, org_id, user_id, plane):
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=org_id, user_id=user_id, plane=plane)
    s.add(conv)
    s.commit()
    return conv.id


def _latest_task(app_mod):
    return app_mod._SessionFactory().query(ScheduledTask).order_by(ScheduledTask.id.desc()).first()


def test_chat_schedule_inherits_org_plane(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _client(tmp_path, monkeypatch, org_backend=True)
    cid = _make_conv(app_mod, org_id, uid, "org")
    r = client.post(
        "/chat/schedule",
        data={"title": "weekly digest", "goal": "summarize the week", "conv_id": str(cid)},
    )
    assert r.status_code == 200
    assert _latest_task(app_mod).plane == "org"  # inherited from the Org chat


def test_chat_schedule_defaults_solo(tmp_path, monkeypatch):
    client, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    cid = _make_conv(app_mod, org_id, uid, "solo")
    client.post("/chat/schedule", data={"title": "t", "goal": "g", "conv_id": str(cid)})
    assert _latest_task(app_mod).plane == "solo"


def test_chat_schedule_no_conv_is_solo(tmp_path, monkeypatch):
    client, app_mod, _, _ = _client(tmp_path, monkeypatch)
    client.post("/chat/schedule", data={"title": "t", "goal": "g"})  # no conv_id
    assert _latest_task(app_mod).plane == "solo"
