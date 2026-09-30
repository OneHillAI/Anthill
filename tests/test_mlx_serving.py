"""On-device MLX serving for the local/solo plane: build + lifecycle of the mlx-lm server, the
MLX eval backend, the local eval-gate (no Ollama), and the executor's promote+serve path.

No real model is loaded and no real server is launched - the subprocess/probe/generate boundaries
are injected so the orchestration logic is what gets tested.
"""

from types import SimpleNamespace

import pytest

from anthill.inference.base import Message
from anthill.lifecycle.evaluate import EvalResult
from anthill.training import mlx_serve, trainer

# ── mlx_serve: command building + server lifecycle ────────────────────────────


def test_build_server_cmd_has_model_adapter_and_port():
    cmd = mlx_serve.build_server_cmd("mlx-community/Foo-4bit", "/a/adapter", "127.0.0.1", 11435)
    assert "server" in cmd
    assert cmd[cmd.index("--model") + 1] == "mlx-community/Foo-4bit"
    assert cmd[cmd.index("--adapter-path") + 1] == "/a/adapter"
    assert cmd[cmd.index("--port") + 1] == "11435"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"


def test_build_server_cmd_can_override_chat_template():
    cmd = mlx_serve.build_server_cmd(
        "mlx-community/Foo-4bit", "/a/adapter", "127.0.0.1", 11435, chat_template="patched"
    )
    assert cmd[cmd.index("--chat-template") + 1] == "patched"


def test_tool_compatible_chat_template_fixes_doubled_json_braces(monkeypatch):
    import sys
    import types

    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = types.SimpleNamespace(
        from_pretrained=lambda repo: types.SimpleNamespace(
            chat_template=r"prefix {{\"name\": <function-name>, \"arguments\": <args-json-object>}} suffix"
        )
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    assert mlx_serve._tool_compatible_chat_template("repo") == (
        r"prefix {\"name\": <function-name>, \"arguments\": <args-json-object>} suffix"
    )


class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False


def test_manager_start_resolves_tag_and_reports_ready(monkeypatch):
    monkeypatch.setattr(mlx_serve, "_tool_compatible_chat_template", lambda _: None)
    mgr = mlx_serve.MlxServeManager()
    proc = _FakeProc(alive=True)
    st = mgr.start(
        "qwen2.5:3b",
        "/a/adapter",
        port=11435,
        spawn=lambda cmd: proc,
        probe=lambda url: True,  # ready on first poll
        sleep=lambda s: None,
    )
    assert st["running"] is True
    assert st["url"] == "http://127.0.0.1:11435/v1"
    assert st["model"] == "mlx-community/Qwen2.5-3B-Instruct-4bit"  # Ollama tag -> mlx repo
    mgr.stop()
    assert proc.terminated is True


def test_manager_start_reports_not_ready_when_process_dies(monkeypatch):
    monkeypatch.setattr(mlx_serve, "_tool_compatible_chat_template", lambda _: None)
    mgr = mlx_serve.MlxServeManager()
    st = mgr.start(
        "qwen2.5:3b",
        "/a/adapter",
        spawn=lambda cmd: _FakeProc(alive=False),  # exits immediately
        probe=lambda url: False,
        sleep=lambda s: None,
    )
    assert st["running"] is False and st["url"] == ""
    assert "exited" in st["detail"]


def test_manager_start_times_out_when_never_ready(monkeypatch):
    monkeypatch.setattr(mlx_serve, "_tool_compatible_chat_template", lambda _: None)
    mgr = mlx_serve.MlxServeManager()
    st = mgr.start(
        "qwen2.5:3b",
        "/a/adapter",
        spawn=lambda cmd: _FakeProc(alive=True),
        probe=lambda url: False,  # never becomes ready
        attempts=3,
        sleep=lambda s: None,
    )
    assert st["running"] is True  # process is alive...
    assert st["url"] == ""  # ...but the endpoint never answered
    assert "did not become ready" in st["detail"]


def test_manager_start_passes_compatible_chat_template(monkeypatch):
    captured = {}
    proc = _FakeProc(alive=True)
    monkeypatch.setattr(mlx_serve, "_tool_compatible_chat_template", lambda _: "patched")
    mgr = mlx_serve.MlxServeManager()

    def spawn(cmd):
        captured["cmd"] = cmd
        return proc

    mgr.start(
        "qwen2.5:3b",
        "/a/adapter",
        spawn=spawn,
        probe=lambda url: True,
        sleep=lambda s: None,
    )
    assert captured["cmd"][captured["cmd"].index("--chat-template") + 1] == "patched"


def test_manager_start_rejects_unmappable_model():
    mgr = mlx_serve.MlxServeManager()
    st = mgr.start("totally-unknown:7b", "/a/adapter", spawn=lambda cmd: _FakeProc())
    assert st["running"] is False and "cannot resolve base model" in st["detail"]


# ── MlxBackend: the eval backend dispatches base vs base+adapter ──────────────


def test_mlx_backend_chat_dispatches_adapter(monkeypatch):
    from anthill.inference import mlx_local

    loaded = []

    def fake_load(base, adapter):
        loaded.append((base, adapter))
        tok = SimpleNamespace(apply_chat_template=lambda msgs, add_generation_prompt: "PROMPT")
        return ("MODEL", tok)

    monkeypatch.setattr(mlx_local, "_load", fake_load)
    monkeypatch.setattr(mlx_local, "_generate", lambda m, t, p, mt: f"ans({p})")

    be = mlx_local.MlxBackend("mlx-community/Foo-4bit")
    # current model = base (no adapter)
    assert be.chat([Message("user", "hi")], model="") == "ans(PROMPT)"
    # candidate = base + adapter path
    assert be.chat([Message("user", "hi")], model="/a/adapter") == "ans(PROMPT)"
    assert loaded == [("mlx-community/Foo-4bit", None), ("mlx-community/Foo-4bit", "/a/adapter")]


def test_mlx_backend_health_flags_missing_mlx(monkeypatch):
    import importlib.util

    from anthill.inference import mlx_local

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "mlx_lm" else object()
    )
    assert "mlx-lm is not installed" in (mlx_local.MlxBackend("x").health() or "")


# ── the local eval-gate (no Ollama registration) ──────────────────────────────


def _eval(monkeypatch, *, score_current, score_candidate):
    def fake_eval(backend, model_a, model_b, examples, *, sample=20):
        return EvalResult(model_a, model_b, 1, score_current, score_candidate, 0, 1, 0)

    monkeypatch.setattr(trainer, "evaluate_models", fake_eval)


def test_promote_local_mlx_keeps_a_winning_adapter(monkeypatch):
    _eval(monkeypatch, score_current=0.70, score_candidate=0.90)
    out = trainer.promote_local_mlx(
        "/a/adapter", base_model="qwen2.5:3b", version=2, eval_examples=[("q", "a")]
    )
    assert out.won_eval is True and out.model_version == 2
    assert "promoted local v2" in out.detail


def test_promote_local_mlx_rejects_a_regression(monkeypatch):
    _eval(monkeypatch, score_current=0.90, score_candidate=0.70)
    out = trainer.promote_local_mlx(
        "/a/adapter", base_model="qwen2.5:3b", version=2, eval_examples=[("q", "a")]
    )
    assert out.won_eval is False and out.model_version is None
    assert "rejected local candidate" in out.detail


# ── executor: promote + serve (or discard) on the local MLX path ──────────────


def _adapter_src(tmp_path):
    src = tmp_path / "fresh-adapter"
    src.mkdir()
    (src / "adapters.safetensors").write_text("weights")
    return str(src)


def test_executor_promotes_persists_and_serves_local_mlx(tmp_path, monkeypatch):
    from anthill.training import executor

    monkeypatch.setenv("ANTHILL_ADAPTERS_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(
        trainer,
        "promote_local_mlx",
        lambda persisted, **k: trainer.TrainOutcome(
            persisted, True, k["version"], "promoted local v1"
        ),
    )
    started = {}
    monkeypatch.setattr(
        mlx_serve.manager,
        "start",
        lambda base, adapter: (
            started.update(base=base, adapter=adapter)
            or {
                "url": "http://127.0.0.1:11435/v1",
                "model": "mlx-community/Qwen2.5-3B-Instruct-4bit",
            }
        ),
    )
    cfg = SimpleNamespace(local_finetune_path="", local_serve_url="", local_serve_model="")
    run = SimpleNamespace(org_id=7, base_model="qwen2.5:3b")

    out = executor._promote_and_serve_local_mlx(cfg, _adapter_src(tmp_path), run, 1, [("q", "a")])

    assert out.won_eval is True
    import os

    assert os.path.isdir(cfg.local_finetune_path)  # adapter persisted out of the temp workdir
    assert cfg.local_finetune_path.endswith("org7-v1")
    assert cfg.local_serve_url == "http://127.0.0.1:11435/v1"
    assert cfg.local_serve_model == "mlx-community/Qwen2.5-3B-Instruct-4bit"
    assert started["adapter"] == cfg.local_finetune_path  # served the persisted copy


def test_executor_discards_a_rejected_local_candidate(tmp_path, monkeypatch):
    from anthill.training import executor

    monkeypatch.setenv("ANTHILL_ADAPTERS_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(
        trainer,
        "promote_local_mlx",
        lambda persisted, **k: trainer.TrainOutcome(
            persisted, False, None, "rejected local candidate"
        ),
    )
    monkeypatch.setattr(
        mlx_serve.manager,
        "start",
        lambda *a, **k: pytest.fail("must not serve a rejected candidate"),
    )
    cfg = SimpleNamespace(local_finetune_path="", local_serve_url="", local_serve_model="")
    run = SimpleNamespace(org_id=7, base_model="qwen2.5:3b")

    out = executor._promote_and_serve_local_mlx(cfg, _adapter_src(tmp_path), run, 1, [("q", "a")])

    assert out.won_eval is False
    import os

    assert cfg.local_finetune_path == "" and cfg.local_serve_url == ""
    assert not os.path.isdir(str(tmp_path / "store" / "org7-v1"))  # discarded


# ── boot hook: re-serve on restart, or clear a stale URL when MLX is gone ──────


def _app_with_org(tmp_path, **cfg_fields):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.db import Organization, OrgSettings

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(OrgSettings(org_id=o.id, **cfg_fields))
    s.commit()
    return app_mod


def test_boot_reserves_a_promoted_local_finetune(tmp_path, monkeypatch):
    from anthill.training import mlx_serve as ms
    from anthill.web.db import OrgSettings

    app_mod = _app_with_org(
        tmp_path, local_finetune_path="/store/org1-v1", deployment_topology="solo"
    )
    monkeypatch.setattr(ms, "serve_available", lambda: True)
    monkeypatch.setattr(
        ms.manager,
        "start",
        lambda base, adapter: {"url": "http://127.0.0.1:11435/v1", "model": "repo"},
    )
    app_mod._autostart_local_serving()
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.local_serve_url == "http://127.0.0.1:11435/v1" and cfg.local_serve_model == "repo"


def test_boot_clears_stale_serve_url_when_mlx_unavailable(tmp_path, monkeypatch):
    from anthill.training import mlx_serve as ms
    from anthill.web.db import OrgSettings

    app_mod = _app_with_org(
        tmp_path,
        local_finetune_path="/store/org1-v1",
        local_serve_url="http://127.0.0.1:11435/v1",  # left over from a previous (MLX-capable) host
        local_serve_model="repo",
    )
    monkeypatch.setattr(ms, "serve_available", lambda: False)
    app_mod._autostart_local_serving()
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.local_serve_url == "" and cfg.local_serve_model == ""  # falls back to Ollama
