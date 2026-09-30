"""Containerized remote-train helper (the shared mechanism for on-prem / cloud GPU backends) and the
on-prem backend that uses it. Orchestration is verified with a mock runner; the live GPU run is
owner-validated (no real host in CI)."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from anthill.training.backends.base import BackendError
from anthill.training.remote import SSHHost, containerized_train, parse_endpoint


def test_parse_endpoint_variants():
    assert parse_endpoint("ssh user@box").host == "user@box"
    assert parse_endpoint("user@box").host == "user@box"
    h = parse_endpoint("user@box:2222")
    assert h.host == "user@box" and h.port == 2222


def _ok_runner_factory(calls):
    def runner(args, timeout=None):
        calls.append(args)
        if "-r" in args:  # the scp fetch: simulate it creating the local adapter dir
            os.makedirs(os.path.join(args[-1], "adapter"), exist_ok=True)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return runner


def test_containerized_train_runs_ship_train_fetch_in_order(tmp_path):
    ds = tmp_path / "gold.jsonl"
    ds.write_text('{"instruction":"q","output":"a"}\n')
    calls: list[list[str]] = []
    adapter = containerized_train(
        SSHHost(host="user@box"),
        dataset_path=str(ds),
        base_model="qwen3:8b",
        image="img:test",
        runner=_ok_runner_factory(calls),
    )
    joined = [" ".join(a) for a in calls]
    assert any("mkdir -p" in j for j in joined)  # workdir
    assert any(j.startswith("scp") and "gold.jsonl" in j for j in joined)  # ship gold
    assert any("docker run --rm --gpus all" in j and "anthill train-adapter" in j for j in joined)
    assert any("img:test" in j and "--base qwen3:8b" in j for j in joined)  # image + base passed
    assert any("-r" in a for a in calls)  # fetch the adapter back
    assert adapter.endswith("/adapter") and os.path.isdir(adapter)


def test_containerized_train_raises_on_a_failed_step(tmp_path):
    ds = tmp_path / "g.jsonl"
    ds.write_text("{}\n")

    def boom(args, timeout=None):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="permission denied")

    with pytest.raises(BackendError):
        containerized_train(SSHHost(host="h"), dataset_path=str(ds), base_model="m", runner=boom)


def test_onprem_backend_uses_the_helper(monkeypatch):
    import anthill.training.remote as remote
    from anthill.training.backends.onprem import OnPremBackend

    monkeypatch.setattr(remote, "containerized_train", lambda h, **kw: "/tmp/x/adapter")
    res = OnPremBackend().run(
        SimpleNamespace(training_gpu_endpoint="user@box"), dataset_path="d.jsonl", base_model="m"
    )
    assert (
        res.adapter_path == "/tmp/x/adapter" and "on-prem" in res.detail and res.cost_usd_est == 0.0
    )


def test_onprem_backend_without_endpoint_refuses(monkeypatch):
    from anthill.training.backends import onprem
    from anthill.training.backends.onprem import OnPremBackend

    # No SSH host AND not a local-trainable Mac -> refuse (the only path that used to exist).
    monkeypatch.setattr(onprem, "local_mac_training_available", lambda: False)
    with pytest.raises(BackendError):
        OnPremBackend().run(
            SimpleNamespace(training_gpu_endpoint=""), dataset_path="d", base_model="m"
        )


def test_train_adapter_cli_invokes_the_trainer(monkeypatch):
    from typer.testing import CliRunner

    import anthill.cli as cli
    import anthill.training.trainer as trainer

    monkeypatch.setattr(
        trainer, "train_adapter", lambda ds, base, *, out_dir, toolchain=None: f"{out_dir}/adapter"
    )
    r = CliRunner().invoke(
        cli.app, ["train-adapter", "--dataset", "g.jsonl", "--base", "m", "--out", "/o"]
    )
    assert r.exit_code == 0 and "/o/adapter" in r.stdout
