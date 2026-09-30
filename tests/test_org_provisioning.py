"""Organization serving-model provisioning: the plan-summary helper + the /settings/organization page.

Direction A: an admin picks where Anthill provisions the org's own model and which model to serve,
and sees the concrete plan (steps + the not-ready escape hatch). Real provisioning ships later.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import provision
from anthill.hosting.provision import ProvisionError
from anthill.web import db as db_mod

# ── plan_summary (pure) ─────────────────────────────────────────────────────────────


def test_plan_summary_vpc_provider():
    s = provision.plan_summary("aws", "qwen2.5:32b", params_b=32.0, region="us-east-1")
    assert s["provider"] == "aws" and s["provider_name"] == "AWS"
    assert s["tier"] == "vpc" and s["tier_name"] == "Cloud VPC"
    assert s["serving_stack"] == "vllm" and s["cold_start"] is False
    assert s["available"] is False and s["readiness"]  # not wired up yet, with a reason
    assert s["escape_hatch"]
    details = " ".join(step["detail"] for step in s["steps"])
    assert "us-east-1" in details and "32B" in details
    assert s["steps"][-1]["name"] == "validate"


def test_plan_summary_neocloud_has_cold_start_and_caveat():
    s = provision.plan_summary("runpod", "llama3.1:8b", params_b=8.0)
    assert s["tier"] == "neocloud" and s["cold_start"] is True
    assert "shared GPUs" in s["notes"]  # amber caveat carried through


def test_plan_summary_rejects_unknown_provider():
    with pytest.raises(ProvisionError):
        provision.plan_summary("openai", "gpt-4o")


# ── /settings/organization route ────────────────────────────────────────────────────


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
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id, "member": member.id}


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_get_renders_provider_picker_for_admin(tmp_path, monkeypatch):
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings/organization")
    assert r.status_code == 200
    assert "Cloud &amp; model" in r.text  # the org cloud model-server page (Model tab)
    assert 'name="org_provider"' in r.text and 'name="org_model"' in r.text
    # every provider + every tier group is offered
    for key in provision.PROVIDER_KEYS:
        assert f'value="{key}"' in r.text
    assert "Cloud VPC" in r.text and "Neocloud" in r.text
    assert 'value="modal"' not in r.text  # Modal is no longer a hosting provider
    assert "EU sovereign" in r.text  # OVHcloud / Scaleway are flagged
    assert "Lambda Labs" in r.text  # the primary Cloud VPC option


def test_advanced_settings_collapsed_by_default_with_provider_note(tmp_path, monkeypatch):
    # Declutter pass: non-functional providers move behind a collapsed "Advanced settings" details,
    # annotated inline so picking one doesn't silently produce a VM that boots but never serves.
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings/organization")
    assert "<summary" in r.text and "Advanced settings" in r.text
    assert "not fully wired up yet, plan preview only" in r.text
    # a functional provider is not annotated as unwired
    assert "Lambda Labs - not fully wired up yet" not in r.text
    # nothing set yet, so Advanced settings should not be forced open
    summary = '<summary style="cursor:pointer;font-weight:600">Advanced settings</summary>'
    assert summary in r.text
    details_start = r.text.rindex("<details", 0, r.text.index(summary))
    tag_end = r.text.index(">", details_start)
    assert "open" not in r.text[details_start:tag_end]


def test_advanced_settings_auto_open_when_already_configured(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    from anthill.web.db import OrgSettings

    s.add(OrgSettings(org_id=ids["org"], org_warm_workers=3))
    s.commit()
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings/organization")
    summary = '<summary style="cursor:pointer;font-weight:600">Advanced settings</summary>'
    details_start = r.text.rindex("<details", 0, r.text.index(summary))
    tag_end = r.text.index(">", details_start)
    assert "open" in r.text[details_start:tag_end]


def test_member_cannot_open_the_page(tmp_path, monkeypatch):
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    r = client.get("/settings/organization", follow_redirects=False)
    assert r.status_code in (302, 303, 403)  # admin-only


def test_post_persists_and_derives_params_then_shows_plan(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "aws", "org_model": "qwen2.5:32b"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "/settings/organization?saved=1" in r.headers["location"]

    cfg = (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == ids["org"])
        .first()
    )
    assert cfg.org_provider == "aws" and cfg.org_model == "qwen2.5:32b"
    assert cfg.org_model_params == "32"  # derived from the model name
    assert cfg.org_backend_status == "planned"

    # the plan now renders on the page (steps + the not-ready escape hatch)
    page = client.get("/settings/organization").text
    assert "Provisioning plan" in page and "validate" in page.lower()
    assert "Not wired up yet" in page


def test_post_rejects_unknown_provider(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "openai", "org_model": "gpt-4o"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=provider" in r.headers["location"]
    cfg = (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == ids["org"])
        .first()
    )
    assert cfg is None or cfg.org_provider == ""  # nothing saved


def test_unchecked_serve_wiki_is_stored_false(tmp_path, monkeypatch):
    # serve_wiki moved to the Wiki tab (POST /settings/organization/wiki)
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization/wiki",
        data={},  # checkbox omitted
        follow_redirects=False,
    )
    cfg = (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == ids["org"])
        .first()
    )
    assert cfg.org_serve_wiki is False


# ── manual endpoint connect + validate (escape hatch) ────────────────────────────────


def _cfg(app_mod, org_id):
    return (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == org_id)
        .first()
    )


def test_connect_validates_and_records_success(tmp_path, monkeypatch):
    # the validate engine's two network calls are module functions -> monkeypatch them (no network)
    from anthill.hosting import endpoint as ep_mod

    monkeypatch.setattr(ep_mod, "_list_models", lambda ep: ["qwen2.5:32b"])
    monkeypatch.setattr(ep_mod, "_round_trip", lambda ep, prompt: "ok")

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    # set the org model first so model_available is exercised
    client.post(
        "/settings/organization",
        data={"org_provider": "aws", "org_model": "qwen2.5:32b"},
        follow_redirects=False,
    )
    r = client.post(
        "/settings/organization/connect",
        data={"org_model_endpoint": "https://gpu.acme.example/v1", "org_model_key": "secret-key"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "connected=ok" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg.org_model_endpoint == "https://gpu.acme.example/v1"
    assert cfg.org_backend_status == "validated"
    assert cfg.org_model_key_enc and cfg.org_model_key_enc != "secret-key"  # stored encrypted


def test_connect_records_failure_detail(tmp_path, monkeypatch):
    from anthill.hosting import endpoint as ep_mod

    def _boom(ep):
        raise OSError("connection refused")

    monkeypatch.setattr(ep_mod, "_list_models", _boom)

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization/connect",
        data={"org_model_endpoint": "https://down.acme.example/v1"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "connected=fail" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg.org_backend_status == "error"
    assert "reachable" in cfg.org_backend_detail  # the failing check is recorded


def test_connect_requires_an_endpoint_url(tmp_path, monkeypatch):
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization/connect", data={"org_model_endpoint": "  "}, follow_redirects=False
    )
    assert r.status_code == 302 and "error=endpoint" in r.headers["location"]


def test_connect_blank_key_keeps_the_saved_one(tmp_path, monkeypatch):
    from anthill.hosting import endpoint as ep_mod

    monkeypatch.setattr(ep_mod, "_list_models", lambda ep: ["m"])
    monkeypatch.setattr(ep_mod, "_round_trip", lambda ep, prompt: "ok")

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization/connect",
        data={"org_model_endpoint": "https://e.example/v1", "org_model_key": "first-key"},
        follow_redirects=False,
    )
    saved = _cfg(app_mod, ids["org"]).org_model_key_enc
    # re-validate with a blank key -> the stored key is unchanged
    client.post(
        "/settings/organization/connect",
        data={"org_model_endpoint": "https://e.example/v1", "org_model_key": ""},
        follow_redirects=False,
    )
    assert _cfg(app_mod, ids["org"]).org_model_key_enc == saved


def test_member_cannot_connect(tmp_path, monkeypatch):
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    r = client.post(
        "/settings/organization/connect",
        data={"org_model_endpoint": "https://e.example/v1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 403)


# ── single-GPU fit gate ───────────────────────────────────────────────────────────────
# The picker greys out a model too big for the chosen GPU, but a direct POST bypasses the UI, and
# provisioning never sets tensor-parallel (single worker). The save route must refuse a selection that
# would provision a pod that then fails to load - the honest capability gate (sizing.servable_on_gpu).


def test_post_rejects_model_too_big_for_gpu(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    # 122B dense on the biggest tier (fp16 ceiling ~68B) with no quant build -> cannot serve on one GPU
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "Qwen3.5 122B", "org_gpu": "141"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=too_big" in r.headers["location"]
    # nothing over-size was persisted (the route returns before commit)
    cfg = _cfg(app_mod, ids["org"])
    assert cfg is None or (cfg.org_model or "") != "Qwen3.5 122B"


def test_post_rejects_when_size_fits_4bit_but_model_has_no_quant_build(tmp_path, monkeypatch):
    # 122B fits the 80 GB *4-bit* ceiling by size, but it has no quant build -> served fp16 (~37B ceiling),
    # so it must be refused even with the 4-bit toggle on. Guards against offering a phantom quant path.
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "Qwen3.5 122B",
            "org_gpu": "80",
            "org_model_quantized": "true",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=too_big" in r.headers["location"]


def test_post_rejects_quant_model_when_gpu_too_small_even_at_4bit(tmp_path, monkeypatch):
    client, _, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    # Llama 3.3 70B has a quant build, but 24 GB can't hold it even at 4-bit (~30B ceiling)
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "Llama 3.3 70B",
            "org_gpu": "24",
            "org_model_quantized": "true",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=too_big" in r.headers["location"]


def test_post_accepts_quant_model_that_fits_at_4bit(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    # Llama 3.3 70B on 48 GB at 4-bit (~70B ceiling) is the actionable path the picker offers - it saves
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "Llama 3.3 70B",
            "org_gpu": "48",
            "org_model_quantized": "true",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg.org_model == "Llama 3.3 70B" and cfg.org_gpu == "48"
    assert cfg.org_model_quantized is True


def test_post_accepts_small_model_that_fits_fp16(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "Gemma 3 4B", "org_gpu": "24"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    assert _cfg(app_mod, ids["org"]).org_model == "Gemma 3 4B"


def test_post_lambda_leaves_training_honestly_unconfigured(tmp_path, monkeypatch):
    # Lambda has no training backend yet (anthill.training.backends.training_backend_for_provider
    # returns ("", "") for it) - saving it as the org's serving provider must not leave a stale
    # training_backend/training_provider behind (was previously only clearing training_provider,
    # per the user's "just the UI/UX fix is not enough, check how training works" ask).
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "Gemma 3 4B", "org_gpu": "24"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg.training_backend == ""
    assert cfg.training_provider == ""


def test_post_switching_org_from_runpod_to_lambda_clears_stale_training_backend(
    tmp_path, monkeypatch
):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    # RunPod first, so it gets a real training_backend/training_provider ...
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Gemma 3 4B", "org_gpu": "24"},
        follow_redirects=False,
    )
    assert _cfg(app_mod, ids["org"]).training_backend == "endpoint"

    # ... then switching to Lambda must clear that stale backend, not just the provider.
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "Gemma 3 4B", "org_gpu": "24"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    fresh = _cfg(app_mod, ids["org"])
    assert fresh.training_backend == ""
    assert fresh.training_provider == ""


def test_post_accepts_a_model_too_big_for_one_gpu_via_a_multi_gpu_instance_type(
    tmp_path, monkeypatch
):
    # docs/specs/multi-gpu-tensor-parallel-serving.md: the same 122B model test_post_rejects_model_too_
    # big_for_gpu refuses on a single 141GB GPU is accepted once a multi-GPU Lambda instance type widens
    # the fit-gate to aggregate VRAM (gpu_count derived from "gpu_8x_h100", not a separate field).
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "Qwen3.5 122B",
            "org_gpu": "141",
            "org_lambda_instance_type": "gpu_8x_h100",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg.org_model == "Qwen3.5 122B" and cfg.org_lambda_instance_type == "gpu_8x_h100"


def test_post_rejects_a_gpu_count_that_does_not_divide_the_models_known_head_count(
    tmp_path, monkeypatch
):
    from anthill.hosting import source

    fake_catalog = [
        source.CatalogModel("Test66Head", 7.0, num_attention_heads=66),
    ]
    monkeypatch.setattr(source, "builtin_catalog", lambda: fake_catalog)
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "Test66Head",
            "org_gpu": "24",
            "org_lambda_instance_type": "gpu_8x_h100",  # 66 heads % 8 != 0
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=gpu_count_mismatch" in r.headers["location"]
    cfg = _cfg(app_mod, ids["org"])
    assert cfg is None or (cfg.org_model or "") != "Test66Head"


def test_post_onprem_skips_the_fit_gate(tmp_path, monkeypatch):
    # On-prem is the org's own box (unknown VRAM) - the gate only guards GPUs we provision, so a big
    # model on-prem still saves.
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "onprem", "org_model": "Qwen3.5 122B", "org_gpu": "141"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    assert _cfg(app_mod, ids["org"]).org_model == "Qwen3.5 122B"
