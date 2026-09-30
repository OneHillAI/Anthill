"""The escalation model picker (#854 follow-up): OrgSettings.escalation_model lets an account
override a provider's curated escalation model, discovered live from the provider's own /v1/models.
Covers the three new pieces _build_attachment_backend/_apply_escalation_attachment/
_annotate_escalation_models don't already have coverage for in test_escalation_attachment_backend.py
and test_escalation_consent_revocation.py.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.inference.openai_compat import OpenAICompatBackend
from anthill.web import db
from anthill.web.app import (
    _INFERENCE_PROVIDERS,
    _annotate_escalation_models,
    _build_attachment_backend,
)
from anthill.web.crypto import encrypt
from anthill.web.db import Organization, OrgSettings, User


class _Cfg:
    def __init__(self, escalation_provider="", escalation_provider_key_enc="", escalation_model=""):
        self.escalation_provider = escalation_provider
        self.escalation_provider_key_enc = escalation_provider_key_enc
        self.escalation_model = escalation_model


def _decrypt(token):
    from anthill.web.crypto import decrypt

    return decrypt(token)


# ── _build_attachment_backend: picked model overrides the curated default ──────────────────────────


def test_picked_model_overrides_the_curated_default():
    cfg = _Cfg(
        escalation_provider="groq",
        escalation_provider_key_enc=encrypt("gsk_secret"),
        escalation_model="llama-3.3-70b-versatile",
    )
    backend = _build_attachment_backend(cfg, _decrypt)
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.model == "llama-3.3-70b-versatile"


def test_blank_picked_model_falls_back_to_the_curated_default():
    cfg = _Cfg(
        escalation_provider="groq",
        escalation_provider_key_enc=encrypt("gsk_secret"),
        escalation_model="",
    )
    backend = _build_attachment_backend(cfg, _decrypt)
    assert backend.model == _INFERENCE_PROVIDERS["groq"]["escalation_model"]


def test_missing_escalation_model_attribute_falls_back_to_the_curated_default():
    # A cfg predating this column (pre-migration in-memory object, or a fake in an older test) has no
    # escalation_model attribute at all - _build_attachment_backend reads it via getattr(..., ""), so
    # this must degrade to the curated default rather than raising.
    class _OldCfg:
        escalation_provider = "groq"
        escalation_provider_key_enc = encrypt("gsk_secret")

    backend = _build_attachment_backend(_OldCfg(), _decrypt)
    assert backend.model == _INFERENCE_PROVIDERS["groq"]["escalation_model"]


# ── _annotate_escalation_models ─────────────────────────────────────────────────────────────────────


def test_annotate_marks_the_curated_default_recommended():
    curated = _INFERENCE_PROVIDERS["groq"]["escalation_model"]
    out = _annotate_escalation_models("groq", [curated, "some-other-model"])
    by_id = {m["id"]: m for m in out}
    assert by_id[curated]["recommended"] is True
    assert by_id["some-other-model"]["recommended"] is False


def test_annotate_flags_known_reasoning_and_slow_families_as_caution():
    out = _annotate_escalation_models(
        "berget", ["deepseek-v3.1", "Kimi-K3", "glm-5.3-flash", "gemma-4-31B-it"]
    )
    by_id = {m["id"]: m for m in out}
    assert by_id["deepseek-v3.1"]["caution"] is True
    assert by_id["Kimi-K3"]["caution"] is True
    assert by_id["glm-5.3-flash"]["caution"] is True
    assert by_id["gemma-4-31B-it"]["caution"] is False


def test_annotate_drops_non_chat_model_ids():
    out = _annotate_escalation_models(
        "groq", ["whisper-large-v3", "text-embedding-3", "llama-3.3-70b"]
    )
    ids = [m["id"] for m in out]
    assert "whisper-large-v3" not in ids
    assert "text-embedding-3" not in ids
    assert "llama-3.3-70b" in ids


def test_annotate_orders_recommended_first_then_plain_then_caution():
    curated = _INFERENCE_PROVIDERS["infercom"]["escalation_model"]
    out = _annotate_escalation_models(
        "infercom", ["deepseek-v3.2", "plain-model-b", curated, "plain-model-a"]
    )
    ids = [m["id"] for m in out]
    assert ids[0] == curated  # recommended first
    assert ids[1:3] == ["plain-model-a", "plain-model-b"]  # plain, alphabetical
    assert ids[3] == "deepseek-v3.2"  # caution last


# ── _apply_escalation_attachment / /personalize/compute: escalation_model persistence ──────────────


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
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
    s.add(OrgSettings(org_id=o.id, deployment_topology="solo"))
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_picking_a_model_stores_it(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_new",
            "escalation_mode": "ask",
            "escalation_model": "llama-3.3-70b-versatile",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_model == "llama-3.3-70b-versatile"


def test_switching_provider_resets_the_picked_model(tmp_path, monkeypatch):
    # A model id from the old provider's catalogue is meaningless for the new one.
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_new",
            "escalation_mode": "ask",
            "escalation_model": "llama-3.3-70b-versatile",
        },
        follow_redirects=False,
    )
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "berget",
            "escalation_api_key": "berget_key",
            "escalation_mode": "ask",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "berget"
    assert cfg.escalation_model == ""


def test_blank_model_on_a_mode_only_resave_keeps_the_existing_choice(tmp_path, monkeypatch):
    # A mode-only re-save (the form always submits escalation_model, blank if the picker was never
    # touched) must not silently clear an already-picked model for the SAME provider.
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_new",
            "escalation_mode": "ask",
            "escalation_model": "llama-3.3-70b-versatile",
        },
        follow_redirects=False,
    )
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "groq",
            "escalation_mode": "automated",
            "escalation_model": "",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_mode == "automated"
    assert cfg.escalation_model == "llama-3.3-70b-versatile"


# ── POST /settings/escalation/discover-models ───────────────────────────────────────────────────────


def test_discover_models_requires_a_provider(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    resp = c.post("/settings/escalation/discover-models", data={})
    assert resp.json()["ok"] is False


def test_discover_models_requires_a_key(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    resp = c.post("/settings/escalation/discover-models", data={"escalation_provider": "groq"})
    assert resp.json()["ok"] is False


def test_discover_models_returns_annotated_live_catalogue(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    monkeypatch.setattr(
        app_mod,
        "_annotate_escalation_models",
        lambda provider, models: [
            {"id": m, "recommended": False, "caution": False} for m in models
        ],
    )
    monkeypatch.setattr(
        "anthill.hosting.endpoint.list_models",
        lambda base_url, api_key: ["model-a", "model-b"],
    )
    c, _ = _client(tmp_path, monkeypatch)
    resp = c.post(
        "/settings/escalation/discover-models",
        data={"escalation_provider": "groq", "escalation_api_key": "gsk_typed"},
    )
    d = resp.json()
    assert d["ok"] is True
    assert [m["id"] for m in d["models"]] == ["model-a", "model-b"]
    assert d["default"] == _INFERENCE_PROVIDERS["groq"]["escalation_model"]


def test_discover_models_surfaces_a_provider_unreachable_error(tmp_path, monkeypatch):
    def _boom(base_url, api_key):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("anthill.hosting.endpoint.list_models", _boom)
    c, _ = _client(tmp_path, monkeypatch)
    resp = c.post(
        "/settings/escalation/discover-models",
        data={"escalation_provider": "groq", "escalation_api_key": "gsk_typed"},
    )
    d = resp.json()
    assert d["ok"] is False
    assert "error" in d
