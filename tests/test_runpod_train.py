"""Live RunPod training orchestration (network mocked): launch a GPU pod -> wait until SSH-reachable ->
train via the shared SSH trainer -> ALWAYS terminate the pod (success or failure). Plus the httpx
pod-GraphQL client. No live account, no spend."""

import pytest

from anthill.training.backends.base import BackendError
from anthill.training.backends.runpod_train import (
    RunpodTrainer,
    _RealRunpodPodClient,
    estimate_cost,
)
from anthill.training.remote import SSHHost


class _FakeClient:
    def __init__(self, *, launch_raises=False, ready_after=1, never_ssh=False):
        self.launch_raises = launch_raises
        self.ready_after = ready_after
        self.never_ssh = never_ssh
        self._polls = 0
        self.launched = None
        self.terminated = []

    def launch(self, *, name, gpu_type, image, disk_gb):
        if self.launch_raises:
            raise RuntimeError("runpod 500")
        self.launched = {"name": name, "gpu_type": gpu_type, "image": image, "disk_gb": disk_gb}
        return "pod-1"

    def ssh_host(self, pod_id):
        self._polls += 1
        if self.never_ssh or self._polls < self.ready_after:
            return None
        return SSHHost(host="root@1.2.3.4", port=12345)

    def terminate(self, pod_id):
        self.terminated.append(pod_id)


def _train_ok(host, *, dataset_path, base_model):
    return "/tmp/adapter"


def test_train_success_returns_adapter_and_tears_down():
    c = _FakeClient(ready_after=1)
    seen = {}

    def train_fn(host, *, dataset_path, base_model):
        seen["host"], seen["ds"], seen["base"] = host, dataset_path, base_model
        return "/tmp/adapter"

    out = RunpodTrainer().train(
        dataset_path="gold.jsonl",
        base_model="qwen2.5:7b",
        client=c,
        train_fn=train_fn,
        sleep=lambda s: None,
    )
    assert out == "/tmp/adapter"
    assert c.launched and c.launched["name"] == "anthill-train-qwen2.5-7b"
    assert seen["host"].host == "root@1.2.3.4" and seen["base"] == "qwen2.5:7b"
    assert c.terminated == ["pod-1"]  # ephemeral: torn down even on success


def test_never_ssh_ready_tears_down():
    c = _FakeClient(never_ssh=True)
    with pytest.raises(BackendError, match="SSH-reachable"):
        RunpodTrainer().train(
            dataset_path="g.jsonl",
            base_model="m",
            client=c,
            train_fn=_train_ok,
            sleep=lambda s: None,
            ready_attempts=3,
        )
    assert c.terminated == ["pod-1"]  # guaranteed teardown of the billable pod


def test_train_failure_tears_down():
    c = _FakeClient(ready_after=1)

    def boom(host, *, dataset_path, base_model):
        raise BackendError("remote step failed")

    with pytest.raises(BackendError, match="remote step failed"):
        RunpodTrainer().train(
            dataset_path="g.jsonl", base_model="m", client=c, train_fn=boom, sleep=lambda s: None
        )
    assert c.terminated == ["pod-1"]  # torn down on failure


def test_launch_failure_leaves_nothing_to_tear_down():
    c = _FakeClient(launch_raises=True)
    with pytest.raises(Exception):  # noqa: B017 - a raw provider error before the pod exists
        RunpodTrainer().train(dataset_path="g.jsonl", base_model="m", client=c, train_fn=_train_ok)
    assert c.terminated == []  # nothing was launched


def test_cost_cap_refuses_before_launch():
    c = _FakeClient()
    with pytest.raises(BackendError, match="exceeds"):
        RunpodTrainer().train(
            dataset_path="g.jsonl",
            base_model="m",
            client=c,
            train_fn=_train_ok,
            max_runtime_min=100000,
            cost_cap_usd=1.0,
        )
    assert c.launched is None and c.terminated == []


def test_no_dataset_rejected():
    c = _FakeClient()
    with pytest.raises(BackendError, match="dataset"):
        RunpodTrainer().train(dataset_path="", base_model="m", client=c, train_fn=_train_ok)
    assert c.launched is None


def test_estimate_cost_scales_with_runtime():
    assert estimate_cost("NVIDIA A40", 120) == pytest.approx(0.78)  # ~$0.39/hr x 2h
    assert estimate_cost("NVIDIA A40", 0) == 0.0


# ── the httpx pod-GraphQL client (network mocked) ─────────────────────────────────────


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._p


def test_real_client_launch_ssh_terminate(monkeypatch):
    import httpx

    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        q = (json or {}).get("query", "")
        calls.append({"q": q, "auth": (headers or {}).get("Authorization")})
        if "podFindAndDeployOnDemand" in q:
            return _Resp({"data": {"podFindAndDeployOnDemand": {"id": "pod-9"}}})
        if "pod(input" in q:
            return _Resp(
                {
                    "data": {
                        "pod": {
                            "runtime": {
                                "ports": [
                                    {
                                        "ip": "5.6.7.8",
                                        "publicPort": 40022,
                                        "privatePort": 22,
                                        "type": "tcp",
                                    }
                                ]
                            }
                        }
                    }
                }
            )
        if "podTerminate" in q:
            return _Resp({"data": {"podTerminate": True}})
        raise AssertionError(f"unexpected {q}")

    monkeypatch.setattr(httpx, "post", _post)
    c = _RealRunpodPodClient("rp-key")
    assert c.launch(name="n", gpu_type="NVIDIA A40", image="img", disk_gb=40) == "pod-9"
    host = c.ssh_host("pod-9")
    assert host and host.host == "root@5.6.7.8" and host.port == 40022
    c.terminate("pod-9")  # must not raise
    assert all(call["auth"] == "Bearer rp-key" for call in calls)  # the org's RunPod key


def test_real_client_ssh_none_until_runtime_ready(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"data": {"pod": {"runtime": None}}}))
    assert _RealRunpodPodClient("k").ssh_host("pod-1") is None  # not reachable yet


def test_real_client_needs_a_key():
    with pytest.raises(BackendError):
        _RealRunpodPodClient("")
