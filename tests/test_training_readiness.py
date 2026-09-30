"""The /training readiness panel: shows what it takes to run automated training (connect a GPU
backend, approve gold, enable training) and whether the org is ready."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, TrainingExample, User


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


def test_training_page_shows_not_ready_and_what_it_takes(tmp_path):
    c, _ = _admin_client(tmp_path)
    body = c.get("/training").text
    assert "Automated training" in body
    assert "not ready" in body
    assert "Connect a GPU backend" in body  # the requirement is spelled out
    assert "Connect your org cloud" in body  # the action when not ready


def test_training_page_ready_with_backend_gold_and_enabled(tmp_path):
    from anthill.web.crypto import encrypt

    c, app_mod = _admin_client(tmp_path)
    # the org cloud (RunPod) IS the training backend (derived); give it a key and enable training
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    if not cfg:
        cfg = OrgSettings(org_id=s.query(Organization).first().id)
        s.add(cfg)
    cfg.org_provider = "runpod"
    cfg.training_backend = "endpoint"
    cfg.training_provider = "runpod"
    cfg.org_provision_key_enc = encrypt("rp-key")
    cfg.training_enabled = True
    s.commit()
    # approve one org-scope gold example
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    s.add(TrainingExample(org_id=org.id, scope="org", quality="gold", instruction="q", output="a"))
    s.commit()

    body = c.get("/training").text
    assert "Automated training" in body
    assert '<span class="badge badge-active">ready</span>' in body
    assert "trainNow()" in body  # the Train now button is offered when ready


def _make_solo(app_mod):
    """Flip the org to the local/solo plane and approve one gold example.

    Personal scope, not org: a solo account trains on its own personal gold (there is no shared
    org model to protect - see executor._gold and model_select.is_solo_account), and this must
    match what /training actually checks or the readiness page would claim "ready" for a run
    that /training/run would then refuse for having zero (personal-scope) gold."""
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    if not cfg:
        cfg = OrgSettings(org_id=s.query(Organization).first().id)
        s.add(cfg)
    cfg.deployment_topology = "solo"
    org = s.query(Organization).first()
    s.add(
        TrainingExample(
            org_id=org.id, scope="personal", quality="gold", instruction="q", output="a"
        )
    )
    s.commit()


def test_local_training_ready_when_the_toolchain_is_installed(tmp_path, monkeypatch):
    # Solo plane + on-device LoRA toolchain present (Apple-Silicon MLX) -> ready, no GPU backend needed.
    from anthill.training import trainer

    c, app_mod = _admin_client(tmp_path)
    _make_solo(app_mod)
    monkeypatch.setattr(trainer, "detect_toolchain", lambda: "mlx")
    body = c.get("/training").text
    assert "This machine (Apple-Silicon MLX)" in body
    assert '<span class="badge badge-active">ready</span>' in body
    assert "trainNow()" in body


def test_local_training_not_ready_when_toolchain_missing(tmp_path, monkeypatch):
    # Solo plane but the packaged app didn't bundle mlx-lm -> honestly "not ready" with install guidance.
    from anthill.training import trainer

    c, app_mod = _admin_client(tmp_path)
    _make_solo(app_mod)
    monkeypatch.setattr(trainer, "detect_toolchain", lambda: None)
    body = c.get("/training").text
    assert "not ready" in body
    assert "On-device LoRA toolchain not installed" in body
    assert "pip install mlx-lm" in body  # the fix is spelled out
