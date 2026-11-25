"""An Ollama that refuses adapters is named as that, and the run no longer depends on it (#273).

Ollama removed `ollama create ... ADAPTER` in 0.34.1 and every later release refuses it. The registration step
(`trainer.register_with_ollama`) names that refusal when only an adapter is available. The training image now also
writes a merged GGUF next to the adapter (tests/test_adapter_merged_gguf_273.py), which every Ollama accepts, so the
executor no longer stops a run because the local Ollama refuses adapters.
"""

import subprocess
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.training import executor, trainer
from anthill.training.backends.base import TrainingResult
from anthill.training.trainer import TrainerError
from anthill.web.db import Base, OrgSettings, TrainingExample, TrainingRun


def _version_reply(monkeypatch, payload):
    """Make the version probe see ``payload`` (a dict, or an exception to raise)."""
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(trainer.httpx, "get", fake_get)
    return seen


@pytest.mark.parametrize(
    "version,accepts",
    [
        ("0.30.10", True),
        ("0.33.0", True),
        ("0.34.0", True),
        ("0.34.1", False),
        ("0.35.2", False),
        ("0.40.7", False),
        ("0.40.16", False),
        ("1.0.0", False),
        ("0.40.16-rc1", False),
    ],
)
def test_the_version_decides_whether_adapters_are_accepted(monkeypatch, version, accepts):
    _version_reply(monkeypatch, {"version": version})
    assert trainer.local_ollama_accepts_adapters() is accepts


@pytest.mark.parametrize(
    "payload",
    [{"version": "dev"}, {}, {"version": None}, ["0.40.16"], httpx.ConnectError("no engine")],
)
def test_an_unreadable_version_is_unknown_not_refused(monkeypatch, payload):
    _version_reply(monkeypatch, payload)
    assert trainer.local_ollama_accepts_adapters() is None


def test_the_probe_asks_the_server_ollama_create_will_use(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    seen = _version_reply(monkeypatch, {"version": "0.30.10"})
    trainer.local_ollama_accepts_adapters()
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11999")
    trainer.local_ollama_accepts_adapters()
    monkeypatch.setenv("OLLAMA_HOST", "http://box.lan:11434")
    trainer.local_ollama_accepts_adapters()
    assert seen == [
        "http://127.0.0.1:11434/api/version",
        "http://127.0.0.1:11999/api/version",
        "http://box.lan:11434/api/version",
    ]


# ── the registration step names the refusal ─────────────────────────────────────


def _create_result(monkeypatch, returncode, stderr="", stdout=""):
    monkeypatch.setattr(
        trainer.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, returncode, stdout, stderr),
    )


def test_a_refused_adapter_is_named_not_reported_as_a_generic_failure(tmp_path, monkeypatch):
    _create_result(monkeypatch, 1, stderr="Error: LoRA adapters are no longer supported\n")
    with pytest.raises(TrainerError) as err:
        trainer.register_with_ollama("org1-model-v1", "qwen3:8b", str(tmp_path / "adapter"))
    assert str(err.value) == trainer.ADAPTERS_REFUSED_NOTE
    assert "older than 0.34.1" in str(err.value) and "failed" not in str(err.value)


def test_any_other_creation_failure_keeps_its_own_message(tmp_path, monkeypatch):
    _create_result(monkeypatch, 1, stderr="Error: model 'qwen3:8b' not found")
    with pytest.raises(TrainerError, match=r"`ollama create org1-model-v1` failed: .*not found"):
        trainer.register_with_ollama("org1-model-v1", "qwen3:8b", str(tmp_path / "adapter"))


def test_a_successful_creation_returns_quietly(tmp_path, monkeypatch):
    _create_result(monkeypatch, 0)
    assert trainer.register_with_ollama("m", "qwen3:8b", str(tmp_path / "adapter")) is None


def test_the_engine_is_found_the_way_the_app_finds_it_not_by_path(tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "find_ollama_bin", lambda: "/data/ollama-runtime/ollama")
    seen = []
    monkeypatch.setattr(
        trainer.subprocess,
        "run",
        lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    trainer.register_with_ollama("m", "qwen3:8b", str(tmp_path / "adapter"))
    assert seen[0][:3] == ["/data/ollama-runtime/ollama", "create", "m"]


def test_a_missing_engine_is_a_plain_message_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "find_ollama_bin", lambda: None)

    def missing(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ollama")

    monkeypatch.setattr(trainer.subprocess, "run", missing)
    with pytest.raises(TrainerError, match="Ollama was not found on this machine"):
        trainer.register_with_ollama("m", "qwen3:8b", str(tmp_path / "adapter"))


# ── the executor stops before the training compute ──────────────────────────────


@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    from fk_seed import seed_org_and_users

    seed_org_and_users(session, user_ids=())
    session.add(OrgSettings(org_id=1, training_base_model="qwen3:8b", training_model_ver=0))
    session.add(
        TrainingExample(
            org_id=1, scope="org", quality="gold", instruction="q", context="", output="a"
        )
    )
    session.commit()
    return session


class _Backend:
    name = "endpoint"

    def __init__(self):
        self.calls = 0

    def run(self, cfg, *, dataset_path, base_model, run=None):
        self.calls += 1
        return TrainingResult(
            adapter_path="/tmp/adapter", won_eval=False, cost_usd_est=0.5, detail=""
        )


def _scheduled_run(db):
    run = TrainingRun(org_id=1, base_model="qwen3:8b", backend="endpoint", gold_count=1)
    run.status = "scheduled"
    db.add(run)
    db.commit()
    return run


def _wire(monkeypatch, accepts, local_mlx=False):
    backend = _Backend()
    monkeypatch.setattr(trainer, "local_ollama_accepts_adapters", lambda: accepts)
    monkeypatch.setattr(executor, "_is_local_mlx", lambda cfg: local_mlx)
    monkeypatch.setattr(executor, "get_backend", lambda cfg: backend)
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, True, k["version"], "promoted v1"),
    )
    return backend


@pytest.mark.parametrize("accepts", [False, True, None])
def test_the_run_goes_ahead_on_any_ollama(db, monkeypatch, accepts):
    # The training image also writes a merged GGUF, which every Ollama accepts (#273), so a refusing engine no
    # longer stops the run before it starts.
    backend = _wire(monkeypatch, accepts=accepts)
    run = _scheduled_run(db)
    executor.execute_run(db, run)
    assert backend.calls == 1 and run.status == "promoted"


def test_the_apple_silicon_local_path_never_asks_ollama(db, monkeypatch):
    backend = _wire(monkeypatch, accepts=False, local_mlx=True)
    monkeypatch.setattr(
        executor,
        "_promote_and_serve_local_mlx",
        lambda *a, **k: trainer.TrainOutcome("/tmp/adapter", True, 1, "promoted locally"),
    )
    run = _scheduled_run(db)
    executor.execute_run(db, run)
    assert backend.calls == 1 and run.status == "promoted"
