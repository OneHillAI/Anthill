"""A Solo account that connects a cloud GPU under "Your cloud" (personalize.html's compute
chooser, POST /personalize/compute) must be able to train on that same connection - the founder's
own report: "if you run a Solo account, you can train your data, but this needs to happen on
RunPod... you don't need to create an organization for that." Before this, training was hard-wired
to `deployment_topology == "solo"` alone (training.model_select.is_local_training), so a Solo
account could never reach the cloud training path no matter what it connected - only an org's
admin-only /settings/organization derived training_backend/training_provider from the chosen
provider. This mirrors that exact derivation into Solo's own compute chooser instead.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, TrainingExample, User


def _client(tmp_path, monkeypatch, *, solo_compute="local", ollama_model=""):
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
    s.add(
        OrgSettings(
            org_id=o.id,
            deployment_topology="solo",
            solo_compute=solo_compute,
            ollama_model=ollama_model,
        )
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_connecting_runpod_under_your_cloud_wires_up_training(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={
            "compute": "cloud",
            "cloud_provider": "runpod",
            "provider_api_key": "rp_test_key",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_provider == "runpod"
    assert cfg.solo_compute == "cloud"
    assert cfg.training_backend == "endpoint"
    assert cfg.training_provider == "runpod"
    assert cfg.training_base_model == "qwen3:8b"


def test_training_readiness_reflects_the_connected_solo_cloud_provider(tmp_path, monkeypatch):
    c, _app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_test_key"},
        follow_redirects=False,
    )
    body = c.get("/training").text
    # Not stuck on the on-device message - it recognizes the connected RunPod account.
    assert "on-device lora toolchain" not in body.lower() or "connect a gpu backend" in body.lower()
    assert "RunPod" in body
    # Solo gets pointed back at its own settings, never the admin-only org page.
    assert "/settings/organization" not in body
    assert "/personalize#model" in body


def test_switching_back_to_your_machine_resets_training_backend(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, solo_compute="cloud", ollama_model="qwen3:8b")
    s = app_mod._SessionFactory()
    row = s.query(OrgSettings).first()
    row.org_provider = "runpod"
    row.training_backend = "endpoint"
    row.training_provider = "runpod"
    s.commit()

    c.post(
        "/personalize/compute",
        data={"compute": "local"},
        follow_redirects=False,
    )
    fresh = app_mod._SessionFactory().query(OrgSettings).first()
    assert fresh.training_backend == "onprem"
    assert fresh.training_provider == ""


def test_connecting_lambda_leaves_training_honestly_unconfigured(tmp_path, monkeypatch):
    # Lambda is a real, fully-supported "Your cloud" SERVING provider, but has no training backend
    # implemented yet (anthill.training.backends.training_backend_for_provider returns ("", "") for
    # it) - confirmed by reading the code, per the user's "just the UI/UX fix is not enough, check
    # how training works" ask. Connecting it must not leave a stale training_backend/provider behind.
    c, app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={
            "compute": "cloud",
            "cloud_provider": "lambda",
            "provider_api_key": "lambda_test_key",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_provider == "lambda"
    assert cfg.training_backend == ""
    assert cfg.training_provider == ""


def test_switching_from_runpod_to_lambda_clears_the_stale_runpod_backend(tmp_path, monkeypatch):
    # The bug: only training_provider was cleared when a provider has no training backend, so
    # switching from RunPod (training_backend="endpoint") to Lambda left training_backend="endpoint"
    # behind - /training would then show RunPod's own connection requirements for a Lambda account.
    c, app_mod = _client(tmp_path, monkeypatch, solo_compute="cloud", ollama_model="qwen3:8b")
    s = app_mod._SessionFactory()
    row = s.query(OrgSettings).first()
    row.org_provider = "runpod"
    row.training_backend = "endpoint"
    row.training_provider = "runpod"
    s.commit()

    c.post(
        "/personalize/compute",
        data={
            "compute": "cloud",
            "cloud_provider": "lambda",
            "provider_api_key": "lambda_test_key",
        },
        follow_redirects=False,
    )
    fresh = app_mod._SessionFactory().query(OrgSettings).first()
    assert fresh.training_backend == ""
    assert fresh.training_provider == ""


def test_training_page_admits_lambda_has_no_training_backend_yet(tmp_path, monkeypatch):
    # Before this fix, an empty training_backend fell back to "onprem" in _training_readiness, so a
    # Solo-cloud Lambda account saw "On-prem GPU box (your hardware) - set the SSH host..." with no
    # mention of Lambda at all. It should now honestly name the connected provider instead.
    c, _app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={
            "compute": "cloud",
            "cloud_provider": "lambda",
            "provider_api_key": "lambda_test_key",
        },
        follow_redirects=False,
    )
    body = c.get("/training").text
    assert "Lambda" in body
    assert "no automated training yet" in body or "doesn't support automated training yet" in body
    assert "on-prem gpu box" not in body.lower()
    assert "ssh host" not in body.lower()


def test_solo_cloud_training_needs_the_explicit_enable_toggle(tmp_path, monkeypatch):
    c, _app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_test_key"},
        follow_redirects=False,
    )
    body = c.get("/training").text
    assert 'name="training_enabled"' in body  # the toggle is offered, unlike the local plane
    assert "always on" not in body.lower()


def test_self_tuning_card_links_to_training_for_solo_cloud(tmp_path, monkeypatch):
    c, _app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_test_key"},
        follow_redirects=False,
    )
    body = c.get("/personalize").text
    assert 'href="/training"' in body
    # not the on-device card's dead-end disabled button for this account
    assert 'id="tune-btn"' not in body


def test_on_device_self_tuning_explains_disabled_train_now_visibly(tmp_path, monkeypatch):
    # Founder report, 2026-09-29: clicking "Train now" with 0 gold examples "doesn't do anything" -
    # the button was correctly disabled, but the only explanation was a hover title, easy to miss.
    # The reason should be readable on the page without hovering. This is the ready-toolchain case
    # (an Apple-Silicon dev box with mlx-lm) - see test_on_device_self_tuning_admits_missing_toolchain
    # for the packaged-app case, where the card can't get this far at all.
    from anthill.training.backends.onprem import OnPremBackend

    monkeypatch.setattr(OnPremBackend, "validate", lambda self, cfg: (True, "ready"))
    c, _app_mod = _client(tmp_path, monkeypatch, solo_compute="local", ollama_model="qwen3:8b")
    body = c.get("/personalize").text
    assert 'id="tune-btn"' in body and "disabled" in body
    assert "Approve some answers as gold first" in body  # visible, not just a hover title


def test_on_device_self_tuning_admits_missing_toolchain(tmp_path, monkeypatch):
    # The packaged app never bundles mlx-lm (scripts/build-sidecar.sh installs docs+mcp only), so
    # "Your machine" self-tuning could never actually run - it just silently failed on click with
    # no explanation surfacing anywhere (live founder report: "didn't let you do shit"). The card
    # must say so up front instead of offering a "Train now" button that always fails.
    c, _app_mod = _client(tmp_path, monkeypatch, solo_compute="local", ollama_model="qwen3:8b")
    body = c.get("/personalize").text
    assert 'id="tune-btn"' not in body
    assert "Not available here" in body
    assert "mlx-lm" in body
    assert 'href="/training"' not in body  # this is the on-device card, not the cloud one


def test_self_tuning_card_shows_gold_count_for_solo_cloud(tmp_path, monkeypatch):
    # UI/UX follow-up: the cloud card previously hardcoded 0 gold examples (the count query was
    # gated to on-device tuning only), so it could never show real readiness - just a bare link out
    # to /training. It should reflect the same personal-scope gold count the on-device card does.
    c, app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_test_key"},
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    org_id = s.query(OrgSettings).first().org_id
    s.add(
        TrainingExample(
            org_id=org_id, scope="personal", quality="gold", instruction="q", output="a"
        )
    )
    s.commit()
    body = c.get("/personalize").text
    tune_start = body.index("<h3>Self-tuning")
    tune_card = body[tune_start : tune_start + 800]
    assert "Approved examples" in tune_card
    assert ">1<" in tune_card


def test_org_cloud_tabs_shows_no_bar_for_solo(tmp_path, monkeypatch):
    # A Solo account has only one page here (Training) - nothing to switch between - so it should
    # get no tab bar at all rather than a one-item bar that looks like a dead nav control.
    c, _app_mod = _client(tmp_path, monkeypatch, ollama_model="qwen3:8b")
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_test_key"},
        follow_redirects=False,
    )
    body = c.get("/training").text
    assert "settings-tabs" not in body


def test_model_storage_card_removed_from_settings(tmp_path, monkeypatch):
    # The separate "Model storage" card was removed (founder 2026-10-01): raw on-disk model
    # management doesn't belong in Anthill's UX. Uninstall now lives on each model in the picker.
    c, _app_mod = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Model storage" not in body  # no standalone storage card in Settings
