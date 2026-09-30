"""Provider-agnostic training backend: registry selection + the §A5 invariants.

Confirms one setting selects the right backend, a new provider plugs in by implementing the
interface only, and the AWS backend tears the GPU down even when the run fails (the
non-negotiable guaranteed-teardown invariant). No real cloud is touched - a fake EC2 stands in.
"""

from types import SimpleNamespace

import pytest

from anthill.cloud import aws as cloud_aws
from anthill.training.backends import BACKEND_KEYS, BackendError, get_backend, normalize_key
from anthill.training.backends.base import TrainingBackend, TrainingResult


def _cfg(**over):
    base = {
        "training_backend": "onprem",
        "training_gpu_endpoint": "",
        "org_id": 7,
        "aws_region": "us-east-1",
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key_enc": "enc",
        "aws_instance_type": "g5.xlarge",
        "aws_ami_id": "ami-123",
        "aws_subnet_id": "",
        "aws_security_group_id": "",
        "aws_max_runtime_min": 120,
        "aws_max_cost_usd": "25.00",
    }
    base.update(over)
    return SimpleNamespace(**base)


# ── registry / selection ──────────────────────────────────────────────────────


@pytest.mark.parametrize("key", BACKEND_KEYS)
def test_every_key_resolves_to_a_matching_backend(key):
    backend = get_backend(_cfg(training_backend=key))
    assert backend.name == key
    assert isinstance(backend, TrainingBackend)  # implements validate + run


def test_vpc_is_back_compat_alias_for_aws():
    assert normalize_key("vpc") == "aws"
    assert get_backend(_cfg(training_backend="vpc")).name == "aws"


def test_blank_or_missing_backend_defaults_to_onprem():
    assert get_backend(_cfg(training_backend="")).name == "onprem"
    assert get_backend(SimpleNamespace()).name == "onprem"  # attr absent entirely


def test_unknown_backend_raises_loudly():
    with pytest.raises(BackendError):
        get_backend(_cfg(training_backend="nope"))


def test_new_provider_plugs_in_by_implementing_the_interface_only():
    class FakeBackend:
        name = "fake"

        def validate(self, cfg):
            return True, "ok"

        def run(self, cfg, *, dataset_path, base_model, run=None):
            return TrainingResult(
                adapter_path="/tmp/a", won_eval=True, cost_usd_est=0.0, detail="fake"
            )

    assert isinstance(FakeBackend(), TrainingBackend)  # structural conformance, no base class


# ── per-backend validate (config presence; no GPU launched) ────────────────────


def test_onprem_validate_requires_endpoint(monkeypatch):
    # No GPU endpoint AND no on-device toolchain -> fails. Pin off the Apple-Silicon local-MLX path so
    # this tests the canonical contract regardless of the dev's machine (on a Mac with mlx-lm installed
    # onprem would instead fall back to local MLX); that local-train delegation is covered in
    # test_mac_backend.py.
    monkeypatch.setattr(
        "anthill.training.backends.onprem.local_mac_training_available", lambda: False
    )
    ok, _ = get_backend(_cfg(training_backend="onprem", training_gpu_endpoint="")).validate(
        _cfg(training_gpu_endpoint="")
    )
    assert not ok
    ok, _ = get_backend(_cfg(training_backend="onprem")).validate(
        _cfg(training_gpu_endpoint="ssh://box")
    )
    assert ok


def test_aws_backend_validate_delegates_to_cloud_aws(monkeypatch):
    called = {}

    def fake_validate(cfg):
        called["hit"] = True
        return True, "Connected"

    monkeypatch.setattr(cloud_aws, "validate", fake_validate)
    ok, detail = get_backend(_cfg(training_backend="aws")).validate(_cfg())
    assert ok and called.get("hit") and detail == "Connected"


# ── the critical invariant: guaranteed teardown even on failure ────────────────


class _FakeEC2:
    def __init__(self):
        self.calls = []
        self.running = []

    def run_instances(self, **kw):
        self.calls.append(("run", kw))
        iid = f"i-{len(self.calls):04d}"
        self.running.append(iid)
        return {"Instances": [{"InstanceId": iid}]}

    def terminate_instances(self, InstanceIds=None):
        self.calls.append(("terminate", InstanceIds))
        self.running = [i for i in self.running if i not in (InstanceIds or [])]
        return {}

    def describe_instances(self, Filters=None):
        return {"Reservations": [{"Instances": [{"InstanceId": i} for i in self.running]}]}


def test_aws_backend_run_tears_down_even_though_trainer_unwired(monkeypatch):
    # The GPU-side trainer isn't wired yet, so run() must fail - but only AFTER the
    # instance it launched is terminated. A failed run never leaks a billable GPU.
    monkeypatch.setattr(cloud_aws, "boto3", object())  # truthy: clears the boto3 guard
    monkeypatch.setattr(cloud_aws, "_secret", lambda cfg: "secret")
    fake = _FakeEC2()
    monkeypatch.setattr(cloud_aws, "_ec2", lambda cfg: fake)

    backend = get_backend(_cfg(training_backend="aws"))
    with pytest.raises(BackendError):
        backend.run(
            _cfg(training_backend="aws"), dataset_path="/tmp/gold.jsonl", base_model="qwen3:8b"
        )

    assert fake.running == []  # GUARANTEED teardown
    kinds = [c[0] for c in fake.calls]
    assert "run" in kinds and "terminate" in kinds
