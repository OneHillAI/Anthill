"""Live-provisioning UI + off-thread runner: save the provider key, run provision()/teardown()
through the tested helper (SDK mocked), and gate the route. No real cloud calls, no spend."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import secure_tunnel
from anthill.hosting.endpoint import Check, Validation
from anthill.hosting.runpod_provision import RunpodDeploy
from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User
from anthill.web.provision_run import provision_org, teardown_org


class _FakeTunnelProc:
    def poll(self):
        return None  # always "running" - this test does not exercise tunnel failure


def _fake_tunnel_spawn(argv):
    return _FakeTunnelProc()


_OK = Validation(True, [Check("round_trip", True, "ok")])


class _FakeClient:
    def __init__(self, *, ready_after=1):
        self.ready_after = ready_after
        self._polls = 0
        self.deleted = []

    def create_serverless_endpoint(
        self,
        *,
        name,
        hf_model,
        max_workers,
        idle_seconds,
        gpu_ids="AMPERE_24",
        hf_token="",
        min_workers=0,
    ):
        self.gpu_ids = gpu_ids
        self.hf_token = hf_token
        self.min_workers = min_workers
        return RunpodDeploy(endpoint_id="ep9", base_url="https://api.runpod.ai/v2/ep9/openai/v1")

    def endpoint_ready(self, endpoint_id):
        self._polls += 1
        return self._polls >= self.ready_after

    def delete_endpoint(self, endpoint_id):
        self.deleted.append(endpoint_id)


def _eng(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def _seed(eng, **cfg_kw):
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    base = {"org_provider": "runpod", "org_model": "qwen2.5:7b", "org_model_params": "7"}
    base.update(cfg_kw)
    s.add(OrgSettings(org_id=org.id, **base))
    s.commit()
    return org.id


def _cfg(eng, org_id):
    return sessionmaker(bind=eng)().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


# ── the off-thread runner (SDK mocked) ────────────────────────────────────────────────


def test_provision_org_success_persists_endpoint_and_handle(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng)
    status = provision_org(
        eng, org_id, client=_FakeClient(), validate=lambda ep: _OK, sleep=lambda s: None
    )
    assert status == "provisioned"
    cfg = _cfg(eng, org_id)
    assert cfg.org_backend_status == "provisioned"
    assert cfg.org_backend_handle == "ep9"
    assert cfg.org_model_endpoint == "https://api.runpod.ai/v2/ep9/openai/v1"


def test_provision_org_failure_records_error(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng)
    fail = Validation(False, [Check("reachable", False, "down")])
    status = provision_org(
        eng, org_id, client=_FakeClient(), validate=lambda ep: fail, sleep=lambda s: None
    )
    assert status == "error"
    cfg = _cfg(eng, org_id)
    assert cfg.org_backend_status == "error" and "down" in cfg.org_backend_detail
    assert cfg.org_model_endpoint == ""  # nothing half-committed


def test_provision_org_without_plan_errors(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, org_model="")  # no model chosen
    assert provision_org(eng, org_id, client=_FakeClient()) == "error"


def test_provision_org_unexpected_error_is_recorded_not_left_provisioning(tmp_path, monkeypatch):
    # A background thread must never strand the org on "provisioning": an error not modeled as a
    # ProvisionError (a raw network fault, a bug) is still written back as a terminal error state.
    eng = _eng(tmp_path)
    org_id = _seed(eng, org_backend_status="provisioning")

    class _Boom:
        key = "runpod"

        def provision(self, spec, **kw):
            raise RuntimeError("kaboom")

    monkeypatch.setattr("anthill.web.provision_run.prov.get_provisioner", lambda p: _Boom())
    status = provision_org(eng, org_id, client=object())  # client set -> skips live-client build
    assert status == "error"
    cfg = _cfg(eng, org_id)
    assert cfg.org_backend_status == "error" and "kaboom" in cfg.org_backend_detail


class _LambdaFakeClient:
    """Records the launch config Lambda receives; comes up active immediately."""

    def __init__(self):
        self.launched = None
        self.terminated = []

    def launch(self, *, region, instance_type, ssh_key_names, startup_script, name):
        self.launched = {
            "region": region,
            "instance_type": instance_type,
            "ssh_key_names": ssh_key_names,
        }
        return "i-1"

    def instance(self, instance_id):
        return ("active", "5.6.7.8")

    def terminate(self, instance_id):
        self.terminated.append(instance_id)


def test_provision_org_runpod_passes_selected_gpu_pool(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, org_provider="runpod", org_gpu="80")
    client = _FakeClient()
    status = provision_org(
        eng, org_id, client=client, validate=lambda ep: _OK, sleep=lambda s: None
    )
    assert status == "provisioned"
    assert client.gpu_ids == "AMPERE_80"  # the chosen GPU tier maps to its RunPod pool


def test_provision_org_runpod_passes_hf_token(tmp_path):
    from anthill.web.crypto import encrypt

    eng = _eng(tmp_path)
    org_id = _seed(eng, org_provider="runpod", org_hf_token_enc=encrypt("hf_live"))
    client = _FakeClient()
    provision_org(eng, org_id, client=client, validate=lambda ep: _OK, sleep=lambda s: None)
    assert client.hf_token == "hf_live"  # the saved HF token reaches the GPU worker for gated pulls


def test_spec_resolves_catalog_name_to_servable_id_per_provider():
    # the admin picks a friendly catalog name; the spec carries the concrete id the provider's stack
    # needs, so the GPU actually loads the model (this is the bug that made "Llama 3.3 70B" fail).
    from anthill.web.provision_run import _spec_from_cfg

    runpod = _spec_from_cfg(OrgSettings(org_provider="runpod", org_model="Llama 3.3 70B"))
    assert runpod.model == "meta-llama/Llama-3.3-70B-Instruct"  # cloud/vLLM -> HF repo id
    onprem = _spec_from_cfg(OrgSettings(org_provider="onprem", org_model="Llama 3.3 70B"))
    assert onprem.model == "llama3.3:70b"  # on-prem -> Ollama tag
    raw = _spec_from_cfg(OrgSettings(org_provider="runpod", org_model="my-org/Custom-7B"))
    assert raw.model == "my-org/Custom-7B"  # a raw id an advanced user typed passes through


def test_provider_provision_kw_lambda_parses_and_omits_blanks():
    from anthill.web.provision_run import _provider_provision_kw

    full = OrgSettings(
        org_provider="lambda",
        org_lambda_ssh_keys=" k1 , , k2 ",
        org_lambda_instance_type=" gpu_1x_h100 ",
    )
    assert _provider_provision_kw(full) == {
        "ssh_key_names": ["k1", "k2"],
        "instance_type": "gpu_1x_h100",
    }
    # blank launch config -> nothing passed, so the provisioner defaults / env fallback apply
    assert _provider_provision_kw(OrgSettings(org_provider="lambda")) == {}
    # other providers take no extra launch kwargs
    assert _provider_provision_kw(OrgSettings(org_provider="runpod")) == {}


# --- PR #661 Tier 3: min_workers (warm pool), RunPod only -------------------------------------------


def test_provider_provision_kw_runpod_includes_warm_workers_when_set():
    from anthill.web.provision_run import _provider_provision_kw

    cfg = OrgSettings(org_provider="runpod", org_warm_workers=2)
    assert _provider_provision_kw(cfg)["min_workers"] == 2


def test_provider_provision_kw_runpod_omits_zero_warm_workers():
    from anthill.web.provision_run import _provider_provision_kw

    cfg = OrgSettings(org_provider="runpod", org_warm_workers=0)
    assert "min_workers" not in _provider_provision_kw(cfg)


def test_provider_provision_kw_lambda_omits_warm_workers_regardless():
    # Lambda is an always-on VM, not scale-to-zero serverless - the concept does not apply there.
    from anthill.web.provision_run import _provider_provision_kw

    cfg = OrgSettings(org_provider="lambda", org_warm_workers=2)
    assert "min_workers" not in _provider_provision_kw(cfg)


def test_provision_org_lambda_passes_saved_launch_config(tmp_path, monkeypatch):
    # Exercises the launch-config plumbing. The endpoint is now reached over a supervised SSH tunnel
    # (docs/specs/llm-endpoint-secure-transport.md) that succeeds by default with no env flags - the
    # transport gate itself is covered in test_lambda_provision.py.
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    eng = _eng(tmp_path)
    org_id = _seed(
        eng,
        org_provider="lambda",
        org_region="us-west-1",
        org_lambda_ssh_keys="key-a, key-b",
        org_lambda_instance_type="gpu_1x_h100",
    )
    client = _LambdaFakeClient()
    status = provision_org(
        eng,
        org_id,
        client=client,
        sleep=lambda s: None,
        tunnel_spawn=_fake_tunnel_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert status == "provisioned"
    assert client.launched["ssh_key_names"] == ["key-a", "key-b"]
    assert client.launched["instance_type"] == "gpu_1x_h100"
    assert client.launched["region"] == "us-west-1"  # region rides on the spec
    cfg = _cfg(eng, org_id)
    assert cfg.org_backend_handle == "i-1"
    assert cfg.org_model_endpoint.startswith("http://127.0.0.1:")
    assert cfg.org_model_endpoint.endswith("/v1")


def test_teardown_org_resets_state(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(
        eng,
        org_backend_status="provisioned",
        org_backend_handle="ep9",
        org_model_endpoint="https://api.runpod.ai/v2/ep9/openai/v1",
    )
    client = _FakeClient()
    status = teardown_org(eng, org_id, client=client)
    assert status == "planned" and client.deleted == ["ep9"]
    cfg = _cfg(eng, org_id)
    assert cfg.org_backend_handle == "" and cfg.org_model_endpoint == ""


# ── routes ────────────────────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch, **cfg_kw):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = _eng(tmp_path)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    base = {"org_provider": "runpod", "org_model": "qwen2.5:7b"}
    base.update(cfg_kw)
    s.add_all([admin, OrgSettings(org_id=org.id, **base)])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, app_mod, org.id


def test_save_provision_key_encrypts(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    r = client.post(
        "/settings/organization/provision-key",
        data={"org_provision_key": "rp-secret"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.org_provision_key_enc and cfg.org_provision_key_enc != "rp-secret"


def test_provision_route_requires_a_key(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch)  # runpod + model but no provision key
    r = client.post("/settings/organization/provision", follow_redirects=False)
    assert r.status_code == 302 and "error=no_key" in r.headers["location"]


def test_provision_route_dispatches_when_ready(tmp_path, monkeypatch):
    from anthill.web.crypto import encrypt

    # provider key present -> the route sets status=provisioning and dispatches the runner
    client, app_mod, org_id = _app(
        tmp_path, monkeypatch, org_provision_key_enc=encrypt("rp-secret")
    )
    monkeypatch.setattr("anthill.web.provision_run.provision_org", lambda *a, **k: "provisioned")
    r = client.post("/settings/organization/provision", follow_redirects=False)
    assert r.status_code == 302 and "provisioning=started" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.org_backend_status == "provisioning"


def test_provision_route_rejects_stub_provider(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="aws")  # aws has no live provisioner
    r = client.post("/settings/organization/provision", follow_redirects=False)
    assert r.status_code == 302 and "error=not_live" in r.headers["location"]


def test_provision_card_shown_for_runpod(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch)
    page = client.get("/settings/organization").text
    assert "Provision it" in page  # the live-provision card heading
    assert 'action="/settings/organization/provision"' in page
    assert "billable GPU" in page  # the cost warning


def test_org_post_saves_lambda_launch_config(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:7b",
            "org_region": "us-west-1",
            "org_lambda_ssh_keys": "my-key",
            "org_lambda_instance_type": "gpu_1x_a10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.org_lambda_ssh_keys == "my-key"
    assert cfg.org_lambda_instance_type == "gpu_1x_a10"


def test_lambda_launch_fields_render_for_lambda(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="lambda")
    page = client.get("/settings/organization").text
    assert 'id="lambda_launch"' in page
    assert 'name="org_lambda_ssh_keys"' in page
    assert 'name="org_lambda_instance_type"' in page


def test_org_page_auto_refreshes_while_provisioning(tmp_path, monkeypatch):
    # while provisioning, the page reloads so the outcome (provisioned/error) surfaces without a manual
    # refresh - this is why the stuck "provisioning started" banner never updated.
    client, _, _ = _app(tmp_path, monkeypatch, org_backend_status="provisioning")
    assert "provision-autorefresh" in client.get("/settings/organization").text


def test_org_page_no_auto_refresh_when_settled(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_backend_status="provisioned")
    assert "provision-autorefresh" not in client.get("/settings/organization").text


def test_gpu_picker_renders_with_fit_data(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    assert 'id="org_gpu"' in page and 'name="org_gpu"' in page
    assert "data-maxparams" in page  # the per-tier fit ceiling the JS filters on
    assert "24 GB" in page and "80 GB" in page  # tier labels (VRAM, not the internal pool id)


def test_save_hf_token_encrypts_and_clears(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)

    def _enc():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .first()
            .org_hf_token_enc
        )

    client.post(
        "/settings/organization/hf-token", data={"org_hf_token": "hf_xyz"}, follow_redirects=False
    )
    assert _enc() and _enc() != "hf_xyz"  # stored encrypted, not plaintext
    client.post(
        "/settings/organization/hf-token", data={"org_hf_token": ""}, follow_redirects=False
    )
    assert _enc() == ""  # sending blank clears it


def test_model_picker_is_a_dropdown_without_a_size_field(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    assert '<select name="org_model"' in page  # a real dropdown, not a hidden type-ahead
    assert 'value="__custom__"' in page  # the "Other - enter a model id" escape
    assert 'name="org_model_params"' not in page  # the manual Size field is removed (auto-derived)


def test_org_post_derives_params_from_catalog_model(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.org_model == "Qwen2.5 14B" and cfg.org_model_params == "14"  # size auto-derived


def test_org_post_custom_model_uses_the_free_text_id(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    client.post(
        "/settings/organization",
        data={
            "org_provider": "runpod",
            "org_model": "__custom__",
            "org_model_custom": "my-org/Custom-7B",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.org_model == "my-org/Custom-7B"  # the raw id, not the "__custom__" sentinel
    assert cfg.org_model_params == "7"  # parsed from the id


def test_custom_saved_model_prefills_the_other_input(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod", org_model="my-org/Custom-7B")
    page = client.get("/settings/organization").text
    assert 'value="__custom__" selected' in page  # the Other option is selected
    assert 'name="org_model_custom"' in page and 'value="my-org/Custom-7B"' in page  # pre-filled


def test_oversize_models_are_flagged_coming_soon(tmp_path, monkeypatch):
    import re

    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    # 70B and 235B exceed the largest GPU's full-precision ceiling -> flagged for the JS to grey out
    assert re.search(r'value="Llama 3\.3 70B"[^>]*data-coming="1"', page)
    assert re.search(r'value="MiniMax M3"[^>]*data-coming="1"', page)
    # a model that fits a GPU is not coming-soon
    assert re.search(r'value="Qwen3\.5 27B"[^>]*data-coming=""', page)


def test_gated_help_and_hf_token_field_render(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    assert 'id="gated_help_btn"' in page and 'id="gated_help"' in page
    assert 'data-gated="1"' in page  # gated models flagged for the JS to surface the help
    # Not a <form> - it sits inside the page's own main form, and a nested <form> is invalid HTML
    # (the browser silently detaches "Save changes" from the outer form). A plain button posts via JS.
    assert 'id="org_hf_token"' in page
    assert "_settingsFormPost('/settings/organization/hf-token'" in page


def test_org_post_saves_gpu_tier_and_rejects_unknown(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)

    def _cfg_now():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .first()
        )

    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B", "org_gpu": "48"},
        follow_redirects=False,
    )
    assert _cfg_now().org_gpu == "48"
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B", "org_gpu": "bogus"},
        follow_redirects=False,
    )
    assert _cfg_now().org_gpu == ""  # an unknown tier is not persisted


def test_org_post_saves_warm_workers(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)

    def _cfg_now():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .first()
        )

    client.post(
        "/settings/organization",
        data={
            "org_provider": "runpod",
            "org_model": "Qwen2.5 14B",
            "org_gpu": "48",
            "org_warm_workers": "3",
        },
        follow_redirects=False,
    )
    assert _cfg_now().org_warm_workers == 3


def test_org_post_clamps_warm_workers_to_a_nonnegative_bounded_range(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)

    def _cfg_now():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .first()
        )

    client.post(
        "/settings/organization",
        data={
            "org_provider": "runpod",
            "org_model": "Qwen2.5 14B",
            "org_gpu": "48",
            "org_warm_workers": "-5",
        },
        follow_redirects=False,
    )
    assert _cfg_now().org_warm_workers == 0
    client.post(
        "/settings/organization",
        data={
            "org_provider": "runpod",
            "org_model": "Qwen2.5 14B",
            "org_gpu": "48",
            "org_warm_workers": "9999",
        },
        follow_redirects=False,
    )
    assert _cfg_now().org_warm_workers == 50


def test_quant_toggle_and_sizing_data_render(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    assert (
        'name="org_model_quantized"' in page and 'id="org_model_quantized"' in page
    )  # the 4-bit toggle
    assert "data-maxparams-q" in page  # the per-GPU 4-bit ceiling the JS switches to
    # the curated big model carries a quant build + the "even at 4-bit too big" flag for the JS
    import re

    assert re.search(r'value="Llama 3\.3 70B"[^>]*data-quant="1"', page)
    assert re.search(r'value="Phi-4 14B"[^>]*data-quant=""', page)  # no curated 4-bit build
    # 70B fits at 4-bit (not coming-q); a frontier giant is too big even at 4-bit (coming-q)
    assert re.search(r'value="Llama 3\.3 70B"[^>]*data-coming-q=""', page)
    assert re.search(r'value="MiniMax M3"[^>]*data-coming-q="1"', page)


def test_org_post_saves_quantized_only_for_a_model_with_a_quant_build(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)

    def _cfg_now():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .first()
        )

    # a curated model with a 4-bit build -> the toggle sticks
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Llama 3.3 70B", "org_model_quantized": "on"},
        follow_redirects=False,
    )
    assert _cfg_now().org_model_quantized is True
    # a model with no quant build -> the flag is dropped (it would be meaningless)
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Llama 3.1 8B", "org_model_quantized": "on"},
        follow_redirects=False,
    )
    assert _cfg_now().org_model_quantized is False
    # unchecked -> off
    client.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Llama 3.3 70B"},
        follow_redirects=False,
    )
    assert _cfg_now().org_model_quantized is False


def test_plan_reflects_selected_gpu_size_neocloud():
    # the VRAM target flows into the plan, so "Save and preview plan" names the GPU the admin picked
    # (not just the picker) - for a serverless neocloud the deploy step carries it.
    from anthill.hosting import provision

    plan = provision.plan_summary("runpod", "qwen2.5:7b", params_b=7, gpu_vram_gb=80)
    deploy = next(s for s in plan["steps"] if s["name"] == "create_deploy")
    assert "80 GB" in deploy["detail"]


def test_plan_reflects_selected_gpu_size_vpc():
    # a cloud VPC launch step names the same provider-agnostic VRAM target
    from anthill.hosting import provision

    plan = provision.plan_summary(
        "aws", "meta-llama/Llama-3.1-8B-Instruct", params_b=8, gpu_vram_gb=48
    )
    launch = next(s for s in plan["steps"] if s["name"] == "launch_instance")
    assert "48 GB" in launch["detail"]


def test_plan_lambda_explicit_instance_overrides_vram_phrase():
    # a typed Lambda instance is an explicit override and wins over the generic VRAM phrase
    from anthill.hosting import provision

    plan = provision.plan_summary(
        "lambda", "qwen2.5:7b", params_b=7, gpu_vram_gb=48, instance_type="gpu_1x_a10"
    )
    launch = next(s for s in plan["steps"] if s["name"] == "launch_instance")
    assert "gpu_1x_a10" in launch["detail"] and "48 GB" not in launch["detail"]


def test_plan_without_a_gpu_choice_falls_back_to_generic_sizing():
    # no GPU picked yet -> the plan still reads sensibly (sized for the model), not a broken blank
    from anthill.hosting import provision

    plan = provision.plan_summary("aws", "meta-llama/Llama-3.1-8B-Instruct", params_b=8)
    launch = next(s for s in plan["steps"] if s["name"] == "launch_instance")
    assert "sized for 8B" in launch["detail"]


def test_org_page_plan_reflects_gpu_choice(tmp_path, monkeypatch):
    # end to end through the route: the rendered plan preview names the selected GPU size
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod", org_gpu="80")
    page = client.get("/settings/organization").text
    assert "80 GB-class GPU" in page


def test_gpu_selector_reframed_as_vram_target(tmp_path, monkeypatch):
    # the selector is a provider-agnostic VRAM target (relabeled from "Cloud GPU"), which is why the
    # same list shows for every provider - the copy explains the mapping instead of looking static.
    client, _, _ = _app(tmp_path, monkeypatch, org_provider="runpod")
    page = client.get("/settings/organization").text
    assert "GPU size (VRAM)" in page
    assert "VRAM target" in page
    assert "Cloud GPU" not in page  # the misleading old label is gone


def test_spec_serves_the_quant_repo_when_org_opts_in(tmp_path):
    # the whole point: with the toggle on, provisioning sends the AWQ repo so vLLM serves it 4-bit and
    # the big model fits a normal GPU - without it, the full-precision repo.
    from anthill.web.provision_run import _spec_from_cfg

    quant = _spec_from_cfg(
        OrgSettings(org_provider="runpod", org_model="Llama 3.3 70B", org_model_quantized=True)
    )
    assert quant.model == "casperhansen/llama-3.3-70b-instruct-awq"
    fp16 = _spec_from_cfg(
        OrgSettings(org_provider="runpod", org_model="Llama 3.3 70B", org_model_quantized=False)
    )
    assert fp16.model == "meta-llama/Llama-3.3-70B-Instruct"
