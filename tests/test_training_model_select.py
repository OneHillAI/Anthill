"""Training always fine-tunes the *served* model, and local/solo training is always-on.

- resolve_base_model: org plane -> the org's selected model; local/solo -> the local model.
- training_on: admin-toggled for an org, always-on for solo.
- The Training UI shows the model read-only (no free-text base-model box) and the run uses it.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.config import DEFAULT_MODEL
from anthill.training.model_select import (
    is_local_training,
    is_solo_account,
    resolve_base_model,
    training_on,
)
from anthill.training.schedule import should_train
from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, TrainingExample, TrainingRun, User


class _Cfg:
    def __init__(self, **kw):
        self.org_model = kw.get("org_model", "")
        self.deployment_topology = kw.get("deployment_topology", "org")
        self.training_enabled = kw.get("training_enabled", False)
        self.solo_compute = kw.get("solo_compute", "local")


def test_resolve_base_model_prefers_org_model():
    assert resolve_base_model(_Cfg(org_model="Qwen2.5 14B")) == "Qwen2.5 14B"


def test_resolve_base_model_falls_back_to_local_model(monkeypatch):
    monkeypatch.delenv("ANTHILL_MODEL", raising=False)
    assert resolve_base_model(_Cfg(org_model="")) == DEFAULT_MODEL


def test_local_solo_training_is_always_on():
    solo = _Cfg(deployment_topology="solo", training_enabled=False)
    assert is_local_training(solo) and training_on(solo)


def test_org_training_follows_the_admin_toggle():
    assert not training_on(_Cfg(deployment_topology="org", training_enabled=False))
    assert training_on(_Cfg(deployment_topology="org", training_enabled=True))


def test_solo_cloud_is_not_local_training_but_is_still_a_solo_account():
    # A solo account that attached a cloud GPU under "Your cloud" trains on that connected GPU,
    # not on-device - is_local_training must say False so the readiness/dispatch machinery routes
    # it to the cloud backend registry, while is_solo_account (gold scope, "does self-tuning apply
    # to me") stays True either way - a solo tenant is one user regardless of where compute runs.
    solo_cloud = _Cfg(deployment_topology="solo", solo_compute="cloud")
    assert is_solo_account(solo_cloud)
    assert not is_local_training(solo_cloud)


def test_solo_cloud_training_needs_the_explicit_toggle_like_org_cloud_does():
    # Cloud training costs real money whether the account is solo or org - it must never be
    # "always on" just because the account happens to be solo.
    solo_cloud = _Cfg(deployment_topology="solo", solo_compute="cloud", training_enabled=False)
    assert not training_on(solo_cloud)
    solo_cloud.training_enabled = True
    assert training_on(solo_cloud)


def test_solo_local_is_both_local_training_and_a_solo_account():
    solo_local = _Cfg(deployment_topology="solo", solo_compute="local")
    assert is_solo_account(solo_local)
    assert is_local_training(solo_local)


def test_org_is_never_a_solo_account_regardless_of_compute():
    org = _Cfg(deployment_topology="org")
    assert not is_solo_account(org)
    assert not is_local_training(org)


def test_should_train_honours_enabled_override():
    # Stored flag off, but the caller passes enabled=True (the local/solo always-on case).
    cfg = _Cfg(training_enabled=False)
    cfg.training_backend = "onprem"
    cfg.training_gpu_endpoint = ""
    cfg.training_gold_mark = 0
    cfg.training_schedule_hrs = 24
    cfg.training_last_run = None
    go, _ = should_train(cfg, gold_count=3, now=datetime.now(timezone.utc), enabled=True)
    assert go
    # Without the override it respects the (off) stored flag.
    off, reason = should_train(cfg, gold_count=3, now=datetime.now(timezone.utc))
    assert not off and reason == "training disabled"


# ── integration: the run + UI use the served model, not a free-text field ────────


def _admin_client(tmp_path, *, topology="org", org_model=""):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(OrgSettings(org_id=o.id, deployment_topology=topology, org_model=org_model))
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def test_training_page_shows_model_readonly_no_freetext(tmp_path):
    c, _app, _ = _admin_client(tmp_path, topology="org", org_model="Qwen2.5 14B")
    body = c.get("/training").text
    assert "Qwen2.5 14B" in body  # the served model is shown
    assert 'name="training_base_model"' not in body  # the free-text box is gone


def test_solo_page_is_always_on(tmp_path):
    c, _app, _ = _admin_client(tmp_path, topology="solo")
    body = c.get("/training").text
    assert "always on" in body.lower()
    assert 'name="training_enabled"' not in body  # no toggle in the local/solo plane


def test_train_now_uses_org_model_as_base(tmp_path, monkeypatch):
    # Stub the executor so no real training launches; just assert the run's base model.
    import anthill.training.executor as executor

    monkeypatch.setattr(executor, "run_scheduled", lambda *a, **k: None, raising=False)
    c, app_mod, org_id = _admin_client(tmp_path, topology="org", org_model="Llama-3.1 8B")
    # Need a gold example so "train now" proceeds.
    s = app_mod._SessionFactory()
    s.add(TrainingExample(org_id=org_id, scope="org", quality="gold", instruction="q", output="a"))
    s.commit()
    r = c.post("/training/run")
    assert r.status_code == 200 and r.json()["ok"]
    run = app_mod._SessionFactory().query(TrainingRun).order_by(TrainingRun.id.desc()).first()
    assert (
        run is not None and run.base_model == "Llama-3.1 8B"
    )  # served model, not qwen3:8b default
