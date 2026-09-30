"""Pre-flight hardening for the launch-critical live training run (#254):

1. the served Ollama tag is mapped to a Hugging Face repo before `from_pretrained` (else the NVIDIA
   run fails at model load);
2. a winning promotion re-points the served model at the new tag (guarded to the local Ollama), so a
   promoted model is actually served, not just registered + version-bumped;
3. a run is claimed atomically (scheduled -> running), so a concurrent pass can't double-provision a GPU.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.training import executor, trainer
from anthill.training.backends.base import TrainingResult
from anthill.training.trainer import TrainerError
from anthill.web.db import Base, OrgSettings, TrainingExample, TrainingRun


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(eng)
    return eng


def _session(engine):
    return sessionmaker(bind=engine)()


def _seed(db):
    from fk_seed import seed_org_and_users

    seed_org_and_users(db, user_ids=())  # org 1 for the org-scoped rows below
    db.add(OrgSettings(org_id=1, training_base_model="qwen3:8b", training_model_ver=0))
    db.add(
        TrainingExample(
            org_id=1, scope="org", quality="gold", instruction="q", context="", output="a"
        )
    )
    run = TrainingRun(org_id=1, base_model="qwen3:8b", backend="endpoint", gold_count=1)
    run.status = "scheduled"
    db.add(run)
    db.commit()
    return run


class _FakeBackend:
    name = "endpoint"

    def __init__(self):
        self.calls = 0

    def run(self, cfg, *, dataset_path, base_model, run=None):
        self.calls += 1
        return TrainingResult(
            adapter_path="/tmp/adapter", won_eval=False, cost_usd_est=0.5, detail=""
        )


def _mock_win(monkeypatch):
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, True, k["version"], "promoted v1"),
    )


# ── 1. base-model tag -> HF repo ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag,repo",
    [
        ("qwen3:8b", "Qwen/Qwen3-8B"),
        ("qwen2.5:3b", "Qwen/Qwen2.5-3B-Instruct"),
        ("llama3.1:8b", "meta-llama/Llama-3.1-8B-Instruct"),
        ("mistral:7b", "mistralai/Mistral-7B-Instruct-v0.3"),
        ("gemma2:9b", "google/gemma-2-9b-it"),
    ],
)
def test_hf_base_model_maps_common_tags(tag, repo):
    assert trainer._hf_base_model(tag) == repo


def test_hf_base_model_passes_through_repo_ids():
    assert trainer._hf_base_model("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"


def test_hf_base_model_env_override(monkeypatch):
    monkeypatch.setenv("ANTHILL_HF_MODEL", "my/exact-repo")
    assert trainer._hf_base_model("qwen3:8b") == "my/exact-repo"


def test_hf_base_model_unmapped_tag_raises_clearly():
    with pytest.raises(TrainerError):
        trainer._hf_base_model("exotic:1b")


# ── 2. serve the promoted model (guarded) ───────────────────────────────────────


def test_activate_serving_local_when_no_org_endpoint():
    cfg = SimpleNamespace(
        ollama_url="http://localhost:11434", org_model_endpoint="", ollama_model="old", org_model=""
    )
    note = executor._activate_org_serving(cfg, "org1-model-v2")
    assert cfg.ollama_model == "org1-model-v2" and "now serving" in note


def test_activate_serving_when_local_ollama_is_the_org_endpoint():
    cfg = SimpleNamespace(
        ollama_url="http://localhost:11434",
        org_model_endpoint="http://localhost:11434/v1",
        ollama_model="x",
        org_model="old",
    )
    note = executor._activate_org_serving(cfg, "org1-model-v2")
    assert cfg.org_model == "org1-model-v2" and "now serving" in note


def test_activate_serving_leaves_a_remote_endpoint_untouched():
    cfg = SimpleNamespace(
        ollama_url="http://localhost:11434",
        org_model_endpoint="https://api.runpod.ai/v2/abc/openai/v1",
        ollama_model="x",
        org_model="old",
    )
    note = executor._activate_org_serving(cfg, "org1-model-v2")
    assert cfg.org_model == "old" and cfg.ollama_model == "x" and "NOT deployed" in note


def test_win_repoints_local_serving_through_execute_run(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)  # default OrgSettings: local Ollama, no separate org endpoint
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    _mock_win(monkeypatch)
    executor.execute_run(db, run)
    cfg = db.query(OrgSettings).filter_by(org_id=1).first()
    assert cfg.ollama_model == "org1-model-v1"  # promoted model is now the served one
    assert cfg.training_model_ver == 1 and "now serving org1-model-v1" in run.eval_note


def test_win_does_not_repoint_a_remote_serving_endpoint(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)
    cfg0 = db.query(OrgSettings).filter_by(org_id=1).first()
    cfg0.org_model_endpoint = "https://api.runpod.ai/v2/abc/openai/v1"
    cfg0.org_model = "qwen3:8b"
    db.commit()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    _mock_win(monkeypatch)
    executor.execute_run(db, run)
    cfg = db.query(OrgSettings).filter_by(org_id=1).first()
    assert (
        cfg.org_model == "qwen3:8b"
    )  # unchanged: the pod doesn't hold the locally registered adapter
    assert (
        cfg.training_model_ver == 1 and "NOT deployed" in run.eval_note
    )  # still recorded as promoted


# ── 3. atomic run claim (no double-provision) ───────────────────────────────────


def test_execute_run_bails_when_already_claimed(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)
    # Simulate another worker having already claimed the run.
    db.query(TrainingRun).filter_by(id=run.id).update({"status": "running"})
    db.commit()
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    monkeypatch.setattr(
        trainer, "promote_if_better", lambda *a, **k: pytest.fail("must not run a claimed run")
    )
    executor.execute_run(db, run)
    assert fake.calls == 0  # the conditional claim failed -> no GPU work started


def test_execute_run_claims_and_runs_a_scheduled_run(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    _mock_win(monkeypatch)
    executor.execute_run(db, run)
    assert fake.calls == 1 and run.status == "promoted"  # normal path still claims + runs
