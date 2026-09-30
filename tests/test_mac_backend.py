"""Apple-Silicon MLX local-train backend: validate/run, registry selection, and the on-prem
delegation (a Mac with no SSH GPU host trains locally instead of refusing).

No real training runs - ``train_adapter`` and the Apple/MLX detection are injected.
"""

from types import SimpleNamespace

import pytest

from anthill.training.backends import BackendError, get_backend, normalize_key
from anthill.training.backends import mac as mac_mod
from anthill.training.backends.base import TrainingBackend
from anthill.training.backends.mac import MacBackend

_CFG = SimpleNamespace(training_gpu_endpoint="")


# ── registry / selection ──────────────────────────────────────────────────────


def test_mac_is_registered_and_aliased():
    assert get_backend(SimpleNamespace(training_backend="mac")).name == "mac"
    assert normalize_key("local") == "mac"  # friendly alias
    assert get_backend(SimpleNamespace(training_backend="local")).name == "mac"
    assert isinstance(MacBackend(), TrainingBackend)  # structural conformance


# ── validate ──────────────────────────────────────────────────────────────────


def test_validate_ok_on_apple_silicon_with_mlx(monkeypatch):
    monkeypatch.setattr(mac_mod, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(mac_mod, "detect_toolchain", lambda: "mlx")
    ok, detail = MacBackend().validate(_CFG)
    assert ok is True and "MLX" in detail and "no cloud" in detail.lower()


def test_validate_fails_off_apple_silicon(monkeypatch):
    monkeypatch.setattr(mac_mod, "is_apple_silicon", lambda: False)
    ok, detail = MacBackend().validate(_CFG)
    assert ok is False and "Apple-Silicon" in detail


def test_validate_fails_without_mlx(monkeypatch):
    monkeypatch.setattr(mac_mod, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(mac_mod, "detect_toolchain", lambda: None)
    ok, detail = MacBackend().validate(_CFG)
    assert ok is False and "mlx-lm" in detail


def test_local_mac_training_available(monkeypatch):
    monkeypatch.setattr(mac_mod, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(mac_mod, "detect_toolchain", lambda: "mlx")
    assert mac_mod.local_mac_training_available() is True
    monkeypatch.setattr(mac_mod, "detect_toolchain", lambda: "peft")  # NVIDIA toolchain, not MLX
    assert mac_mod.local_mac_training_available() is False


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_trains_locally_and_lets_executor_eval_gate(monkeypatch):
    seen = {}

    def fake_train(dataset_path, base_model, *, out_dir, toolchain=None, **kw):
        seen.update(dataset_path=dataset_path, base_model=base_model, toolchain=toolchain)
        return out_dir + "/adapter"

    monkeypatch.setattr(mac_mod, "train_adapter", fake_train)
    res = MacBackend().run(_CFG, dataset_path="/work/gold.jsonl", base_model="qwen2.5:3b")
    assert seen == {
        "dataset_path": "/work/gold.jsonl",
        "base_model": "qwen2.5:3b",
        "toolchain": "mlx",
    }
    assert res.adapter_path == "/work/mlx-adapter/adapter"
    assert res.won_eval is False  # the executor eval-gates org-side
    assert res.cost_usd_est == 0.0


def test_run_wraps_trainer_errors(monkeypatch):
    def boom(*a, **k):
        raise mac_mod.TrainerError("mlx-lm not installed")

    monkeypatch.setattr(mac_mod, "train_adapter", boom)
    with pytest.raises(BackendError):
        MacBackend().run(_CFG, dataset_path="d.jsonl", base_model="m")


# ── on-prem delegation ────────────────────────────────────────────────────────


def test_onprem_trains_locally_on_a_mac_without_an_endpoint(monkeypatch):
    from anthill.training.backends import onprem

    monkeypatch.setattr(onprem, "local_mac_training_available", lambda: True)
    monkeypatch.setattr(
        onprem.MacBackend,
        "run",
        lambda self, cfg, *, dataset_path, base_model, run=None: "LOCAL",
    )
    out = onprem.OnPremBackend().run(
        SimpleNamespace(training_gpu_endpoint=""), dataset_path="d", base_model="m"
    )
    assert out == "LOCAL"  # delegated to the Mac path, no SSH


def test_onprem_validate_reports_local_mac_when_no_endpoint(monkeypatch):
    from anthill.training.backends import onprem

    monkeypatch.setattr(onprem, "local_mac_training_available", lambda: True)
    monkeypatch.setattr(
        onprem.MacBackend,
        "validate",
        lambda self, cfg: (True, "Apple-Silicon MLX local training ready"),
    )
    ok, detail = onprem.OnPremBackend().validate(SimpleNamespace(training_gpu_endpoint=""))
    assert ok is True and "MLX" in detail
