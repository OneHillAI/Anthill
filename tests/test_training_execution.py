"""Training executor + Modal endpoint: the org-side execution of a scheduled run.

The GPU work and the eval-gate are mocked; what's verified is the orchestration that must be
right - gold is **PII-scrubbed before it leaves the node**, a winning adapter promotes the org
model version, a losing one doesn't, failures are recorded (never raised), and the neocloud
endpoint honors its cost guardrail.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.training import executor, trainer
from anthill.training.backends import BackendError
from anthill.training.backends import endpoint as endpoint_mod
from anthill.training.backends.base import TrainingResult
from anthill.web.db import Base, OrgSettings, TrainingExample, TrainingRun


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(eng)
    return eng


def _session(engine):
    return sessionmaker(bind=engine)()


def _seed(db, *, gold=(("What is our refund window?", "wiki ctx", "30 days."),)):
    from fk_seed import seed_org_and_users

    seed_org_and_users(db, user_ids=())  # org 1 must exist for the org-scoped rows below
    db.add(
        OrgSettings(
            org_id=1,
            training_backend="endpoint",
            training_provider="modal",
            training_base_model="qwen3:8b",
            training_model_ver=0,
        )
    )
    for instr, ctx, out in gold:
        db.add(
            TrainingExample(
                org_id=1, scope="org", quality="gold", instruction=instr, context=ctx, output=out
            )
        )
    run = TrainingRun(org_id=1, base_model="qwen3:8b", backend="endpoint", gold_count=len(gold))
    run.status = "scheduled"
    db.add(run)
    db.commit()
    return run


class _FakeBackend:
    name = "endpoint"

    def __init__(self):
        self.dataset_seen = None

    def run(self, cfg, *, dataset_path, base_model, run=None):
        self.dataset_seen = dataset_path
        return TrainingResult(
            adapter_path="/tmp/adapter", won_eval=False, cost_usd_est=0.5, detail="ok"
        )


# ── train/eval split (the promotion gate must score held-out gold, not trained rows) ──


def _rows(*instructions):
    return [
        TrainingExample(
            org_id=1, scope="org", quality="gold", instruction=i, context="", output="a"
        )
        for i in instructions
    ]


def test_split_gold_holds_out_a_disjoint_eval_slice():
    rows = _rows(*[f"question {n}" for n in range(10)])
    train, ev = executor._split_gold(rows)
    train_q = {r.instruction for r in train}
    eval_q = {r.instruction for r in ev}
    assert train_q.isdisjoint(eval_q)  # no trained row is ever evaluated
    assert train_q | eval_q == {r.instruction for r in rows}  # nothing dropped
    assert len(eval_q) == 2  # ~20% of 10
    assert executor._split_gold(rows)[1] == ev  # deterministic


def test_split_gold_keeps_duplicate_instructions_on_one_side():
    rows = _rows("dup", "dup", "dup", "a", "b", "c", "d", "e")  # 6 distinct -> real split
    train, _ev = executor._split_gold(rows)
    sides = {"train" if r in train else "eval" for r in rows if r.instruction == "dup"}
    assert len(sides) == 1  # all three "dup" rows land together, never straddling


def test_split_gold_small_data_falls_back_to_no_holdout():
    rows = _rows("only", "two", "three")  # 3 distinct < threshold
    train, ev = executor._split_gold(rows)
    assert train == rows and ev == []  # can't carve a trustworthy holdout


# ── executor orchestration ────────────────────────────────────────────────────


def test_promotes_and_bumps_version_when_adapter_wins(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, True, k["version"], "promoted"),
    )
    executor.execute_run(db, run)
    assert run.status == "promoted" and run.model_version == 1
    assert db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver == 1


def test_rejects_without_bumping_version_when_adapter_loses(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db)
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, False, None, "rejected"),
    )
    executor.execute_run(db, run)
    assert run.status == "rejected" and run.model_version is None
    assert db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver == 0


def test_trains_on_train_slice_and_evaluates_on_heldout(engine, monkeypatch):
    db = _session(engine)
    gold = tuple((f"q{n}", "", f"a{n}") for n in range(10))  # 10 distinct -> real split
    run = _seed(db, gold=gold)
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    captured = {}

    def _spy(adapter, **k):
        captured["examples"] = k["eval_examples"]
        return trainer.TrainOutcome(adapter, True, k["version"], "ok")

    monkeypatch.setattr(trainer, "promote_if_better", _spy)
    executor.execute_run(db, run)

    with open(fake.dataset_seen) as fh:
        trained = {json.loads(line)["instruction"] for line in fh}
    evaluated = {instr for instr, _ in captured["examples"]}
    assert trained.isdisjoint(evaluated)  # the gate never scores a trained row
    assert trained | evaluated == {f"q{n}" for n in range(10)}


def test_first_model_promotes_unvalidated_below_split_threshold(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db, gold=(("only q", "", "a"),))  # 1 gold, no incumbent (ver 0)
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    captured = {}

    def _spy(adapter, **k):
        captured["examples"] = k["eval_examples"]
        captured["allow_unvalidated"] = k["allow_unvalidated"]
        return trainer.TrainOutcome(adapter, k["allow_unvalidated"], k["version"], "ok")

    monkeypatch.setattr(trainer, "promote_if_better", _spy)
    executor.execute_run(db, run)
    assert captured["examples"] == []  # nothing to hold out
    assert captured["allow_unvalidated"] is True  # first model: allowed through
    assert run.status == "promoted"


def test_incumbent_rejects_when_gold_below_split_threshold(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db, gold=(("only q", "", "a"),))
    db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver = 4  # live model exists
    db.commit()
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    monkeypatch.setattr(
        trainer, "promote_if_better", lambda *a, **k: pytest.fail("gate must not be reached")
    )
    executor.execute_run(db, run)
    assert fake.dataset_seen is None  # rejection was predetermined: no training compute spent
    assert run.status == "rejected" and "too little gold" in run.eval_note
    cfg = db.query(OrgSettings).filter_by(org_id=1).first()
    assert cfg.training_model_ver == 4 and cfg.training_status == "idle"


def test_incumbent_rejects_when_heldout_slice_too_small(engine, monkeypatch):
    db = _session(engine)
    gold = tuple((f"q{n}", "", f"a{n}") for n in range(10))  # 10 distinct -> only ~2 held out
    run = _seed(db, gold=gold)
    db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver = 3  # live model exists
    db.commit()
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda *a, **k: pytest.fail("the gate must not decide on a tiny held-out slice"),
    )
    executor.execute_run(db, run)
    assert fake.dataset_seen is None  # no training compute spent on a coin-flip
    assert run.status == "rejected" and "trustworthy eval slice" in run.eval_note
    assert (
        db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver == 3
    )  # incumbent kept


def test_validated_promotion_proceeds_with_enough_heldout(engine, monkeypatch):
    db = _session(engine)
    gold = tuple((f"q{n}", "", f"a{n}") for n in range(30))  # 30 distinct -> ~6 held out (>= floor)
    run = _seed(db, gold=gold)
    db.query(OrgSettings).filter_by(org_id=1).first().training_model_ver = 3  # live model exists
    db.commit()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    captured = {}

    def _spy(adapter, **k):
        captured["n"] = len({instr for instr, _ in k["eval_examples"]})
        captured["allow_unvalidated"] = k["allow_unvalidated"]
        return trainer.TrainOutcome(adapter, True, k["version"], "promoted")

    monkeypatch.setattr(trainer, "promote_if_better", _spy)
    executor.execute_run(db, run)
    assert captured["n"] >= executor.MIN_EVAL_EXAMPLES  # gate reached with a real held-out slice
    assert captured["allow_unvalidated"] is False  # replacing a live model must be validated
    assert run.status == "promoted" and run.model_version == 4


def test_records_failure_when_no_gold(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db, gold=())  # no gold examples
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    executor.execute_run(db, run)  # must not raise
    assert run.status == "failed" and "gold" in (run.eval_note or "")


def test_gold_is_pii_scrubbed_before_it_leaves(engine, monkeypatch):
    db = _session(engine)
    run = _seed(db, gold=(("Email bob@acme.com for refunds", "", "We reply in 30 days."),))
    fake = _FakeBackend()
    monkeypatch.setattr(executor, "get_backend", lambda cfg: fake)
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, True, 1, "ok"),
    )
    executor.execute_run(db, run)
    with open(fake.dataset_seen) as fh:
        sent = fh.read()
    assert "bob@acme.com" not in sent  # the raw PII never reaches the (rented) GPU


def test_run_scheduled_executes_queued_runs(engine, monkeypatch):
    db = _session(engine)
    _seed(db)
    monkeypatch.setattr(executor, "get_backend", lambda cfg: _FakeBackend())
    monkeypatch.setattr(
        trainer,
        "promote_if_better",
        lambda adapter, **k: trainer.TrainOutcome(adapter, True, 1, "ok"),
    )
    assert executor.run_scheduled(engine) == 1
    assert _session(engine).query(TrainingRun).filter_by(org_id=1).first().status == "promoted"


# ── Modal endpoint backend ────────────────────────────────────────────────────


def _ecfg(**over):
    base = {
        "training_backend": "endpoint",
        "training_provider": "modal",
        "training_gpu_endpoint": "",
        "training_api_key_enc": "enc",
        "aws_max_runtime_min": 60,
        "aws_max_cost_usd": "25.00",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_endpoint_run_invokes_modal_and_returns_the_adapter(monkeypatch):
    monkeypatch.setattr(endpoint_mod, "_modal_train", lambda cfg, ds, base, **k: "/tmp/adapter")
    res = endpoint_mod.EndpointBackend().run(
        _ecfg(), dataset_path="/tmp/g.jsonl", base_model="qwen3:8b"
    )
    assert res.adapter_path == "/tmp/adapter" and res.won_eval is False and res.cost_usd_est > 0


def test_endpoint_run_refuses_over_budget(monkeypatch):
    monkeypatch.setattr(
        endpoint_mod, "_modal_train", lambda *a, **k: "/tmp/x"
    )  # must not be called
    with pytest.raises(BackendError):
        endpoint_mod.EndpointBackend().run(
            _ecfg(aws_max_runtime_min=10000, aws_max_cost_usd="1"),
            dataset_path="d",
            base_model="b",
        )


def test_endpoint_run_rejects_runpod_until_implemented():
    with pytest.raises(BackendError):
        endpoint_mod.EndpointBackend().run(
            _ecfg(training_provider="runpod"), dataset_path="d", base_model="b"
        )
