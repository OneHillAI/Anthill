"""Modal credential wiring (the saved token id + secret are loaded into the env Modal reads),
honest endpoint validation, the Settings form storing the token id, and the /backend info page."""

from types import SimpleNamespace

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


def test_load_modal_env_sets_both_credentials(tmp_path, monkeypatch):
    from anthill.training.backends.endpoint import load_modal_env
    from anthill.web.crypto import encrypt

    monkeypatch.setenv("MODAL_TOKEN_ID", "")  # register for clean teardown
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "")
    cfg = SimpleNamespace(training_token_id="ak-abc", training_api_key_enc=encrypt("as-xyz"))

    import os

    ready, _ = load_modal_env(cfg)
    assert (
        ready
        and os.environ["MODAL_TOKEN_ID"] == "ak-abc"
        and os.environ["MODAL_TOKEN_SECRET"] == "as-xyz"
    )


def test_load_modal_env_reports_missing(tmp_path):
    from anthill.training.backends.endpoint import load_modal_env

    ready, detail = load_modal_env(
        SimpleNamespace(training_token_id="ak-only", training_api_key_enc="")
    )
    assert not ready and "secret" in detail.lower()


def test_endpoint_validate_is_honest(tmp_path):
    from anthill.training.backends.endpoint import EndpointBackend
    from anthill.web.crypto import encrypt

    b = EndpointBackend()
    # no creds -> actionable error, points at `modal token new`
    ok, detail = b.validate(
        SimpleNamespace(training_provider="modal", training_token_id="", training_api_key_enc="")
    )
    assert not ok and "modal token new" in detail
    # creds present -> ok, and it does NOT falsely claim a verified connection; points to Train now
    ok, detail = b.validate(
        SimpleNamespace(
            training_provider="modal",
            training_token_id="ak-1",
            training_api_key_enc=encrypt("as-1"),
        )
    )
    assert ok and "Train now" in detail and "pending" not in detail.lower()


def test_settings_stores_modal_token_id(tmp_path):
    # the Modal neocloud token now lives with the training settings (Training data page)
    c, app_mod = _admin_client(tmp_path)
    r = c.post(
        "/training/config",
        data={
            "training_base_model": "qwen3:8b",
            "training_schedule_hrs": "24",
            "training_token_id": "ak-pub",
            "training_api_key": "as-secret",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.training_token_id == "ak-pub"
    assert (
        cfg.training_api_key_enc and "as-secret" not in cfg.training_api_key_enc
    )  # secret encrypted


def test_backend_info_page_renders(tmp_path):
    c, _ = _admin_client(tmp_path)
    r = c.get("/backend")
    assert r.status_code == 200
    # /backend is now the Backend sub-page of the Organization rail item
    assert (
        ">Backend<" in r.text and "Dashboard URL" in r.text and "this app is the backend" in r.text
    )


def test_runpod_is_the_standard_training_provider():
    import pytest

    from anthill.training.backends.base import BackendError
    from anthill.training.backends.endpoint import (
        _DEFAULT_PROVIDER,
        PROVIDERS,
        EndpointBackend,
    )
    from anthill.web.crypto import encrypt

    assert _DEFAULT_PROVIDER == "runpod" and PROVIDERS[0] == "runpod"
    b = EndpointBackend()

    # provider unset -> defaults to RunPod, which needs the org's cloud RunPod key (Organization)
    ok, detail = b.validate(SimpleNamespace(training_provider="", org_provision_key_enc=""))
    assert not ok and "Organization" in detail and "RunPod" in detail

    # the cloud RunPod key present -> ready, training reuses that same account (aligned with serving)
    cfg = SimpleNamespace(training_provider="runpod", org_provision_key_enc=encrypt("rp-key"))
    ok, detail = b.validate(cfg)
    assert ok and "cloud RunPod account" in detail

    # run() without a key fails clearly at the org-cloud setup, never silently or "use modal"
    # (the full launch -> train -> teardown orchestration is covered in test_runpod_train.py)
    with pytest.raises(BackendError) as ei:
        b.run(
            SimpleNamespace(training_provider="runpod", org_provision_key_enc=""),
            dataset_path="x.jsonl",
            base_model="qwen2.5:7b",
        )
    assert "No RunPod API key" in str(ei.value) and "modal" not in str(ei.value).lower()
