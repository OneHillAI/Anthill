"""Training-backend Settings routes: save (provider + encrypted key), per-backend test, and
the manual "train now" trigger. The executor is stubbed for the run trigger so the route is
tested without launching anything."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, TrainingExample, TrainingRun, User


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


def _set_org_cloud(app_mod, **fields):
    """Set the org cloud directly (training backend is derived from it, not the Settings form)."""
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    if not cfg:
        org = s.query(Organization).first()
        cfg = OrgSettings(org_id=org.id)
        s.add(cfg)
    for k, v in fields.items():
        setattr(cfg, k, v)
    s.commit()


def test_training_backend_follows_the_org_cloud(tmp_path):
    # training is NOT a separate picker: setting the org cloud derives the training backend
    c, app_mod = _admin_client(tmp_path)
    c.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.training_backend == "endpoint" and cfg.training_provider == "runpod"
    # switching the org cloud to on-prem switches training too
    c.post(
        "/settings/organization",
        data={"org_provider": "onprem", "org_model": "qwen2.5:7b"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.training_backend == "onprem" and cfg.training_provider == ""


def test_training_test_route_validates_the_org_cloud_backend(tmp_path):
    from anthill.web.crypto import encrypt

    c, app_mod = _admin_client(tmp_path)
    # org cloud = RunPod with a saved key -> training reuses it and validates ready
    _set_org_cloud(
        app_mod,
        org_provider="runpod",
        training_backend="endpoint",
        training_provider="runpod",
        org_provision_key_enc=encrypt("rp-key"),
    )
    r = c.post("/settings/training/test", follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"]  # JSON now (toast feedback), not a redirect
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.training_status == "validated"  # the org RunPod key is present -> ok
    assert cfg.training_status_detail


def test_training_test_reports_error_for_misconfigured_backend(tmp_path, monkeypatch):
    # on-prem with no GPU endpoint AND no on-device toolchain -> validate fails. Pin off the
    # Apple-Silicon local-MLX path so this is deterministic anywhere (on a Mac with mlx-lm, onprem
    # with no endpoint validly trains locally).
    monkeypatch.setattr(
        "anthill.training.backends.onprem.local_mac_training_available", lambda: False
    )
    c, app_mod = _admin_client(tmp_path)
    c.post("/settings", data={"training_backend": "onprem"}, follow_redirects=False)
    r = c.post("/settings/training/test", follow_redirects=False)
    assert r.status_code == 200 and not r.json()["ok"]  # JSON; backend misconfigured
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.training_status == "error" and cfg.training_status_detail


def test_train_now_queues_a_run(tmp_path, monkeypatch):
    c, app_mod = _admin_client(tmp_path)
    _set_org_cloud(app_mod, training_backend="endpoint", training_provider="runpod")
    # gold is required to train on; without it the run is refused (see the no-gold test below)
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    s.add(TrainingExample(org_id=org.id, scope="org", quality="gold", instruction="q", output="a"))
    s.commit()

    import anthill.training.executor as ex

    monkeypatch.setattr(ex, "run_scheduled", lambda eng: None)  # don't actually execute
    r = c.post("/training/run", follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"]
    runs = app_mod._SessionFactory().query(TrainingRun).all()
    assert len(runs) == 1 and runs[0].backend == "endpoint" and runs[0].note == "manual run"


def test_train_now_with_no_gold_is_refused_with_a_clear_message(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post("/settings", data={"training_backend": "endpoint"}, follow_redirects=False)
    r = c.post("/training/run", follow_redirects=False)  # zero gold examples
    assert r.status_code == 200 and not r.json()["ok"]
    assert "nothing to train on" in r.json()["detail"].lower()
    assert app_mod._SessionFactory().query(TrainingRun).count() == 0  # no empty run queued


def test_training_backend_for_provider_mapping():
    from anthill.training.backends import training_backend_for_provider

    assert training_backend_for_provider("runpod") == ("endpoint", "runpod")
    assert training_backend_for_provider("onprem") == ("onprem", "")
    assert training_backend_for_provider("aws") == ("aws", "")
    assert training_backend_for_provider("AWS ") == ("aws", "")  # normalized
    # serving providers without a training backend yet -> not available
    assert training_backend_for_provider("lambda") == ("", "")
    assert training_backend_for_provider("ovh") == ("", "")
    assert training_backend_for_provider("") == ("", "")
