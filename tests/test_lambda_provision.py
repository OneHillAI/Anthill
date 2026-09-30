"""Live Lambda Labs provisioner (the network is mocked): launch -> poll-active -> tunnel -> endpoint,
with a guaranteed terminate on any post-launch failure so no billable VM is left behind. Plus the httpx
Cloud-API client.

The endpoint is reached over a supervised SSH tunnel (Option A,
docs/specs/llm-endpoint-secure-transport.md), so every test that reaches the tunnel-establishment step
injects a fake ``tunnel_spawn`` - a real ``ssh`` subprocess must never run in a unit test. The former
"cleartext endpoint refused/allowed by env flag" scenario for Lambda specifically no longer applies: the
tunnel makes the default path secure, so there is no more insecure fallback to gate. That scenario is
still covered where it still applies - the not-yet-tunneled DataCrunch provisioner
(tests/test_datacrunch_provision.py) - and endpoint_is_secure()'s loopback/localhost/spoof-host logic is
covered directly in tests/test_hosting_provision.py.

Also covers single-node tensor-parallel serving (docs/specs/multi-gpu-tensor-parallel-serving.md):
gpu_count is derived from the Lambda instance type (never a separate admin-set field), so every test
exercising it sets ``instance_type=`` rather than passing a new kwarg.
"""

import pytest

from anthill.hosting import provision, secure_tunnel, sizing
from anthill.hosting.lambda_provision import (
    LambdaLiveProvisioner,
    _RealLambdaClient,
    gpu_count_from_instance_type,
    vllm_startup_script,
)


def _spec(**kw):
    base = {"provider": "lambda", "model": "qwen2.5:7b", "params_b": 7.0}
    base.update(kw)
    return provision.ProvisionSpec(**base)


class _FakeTunnelProc:
    """A fake tunnel subprocess, mirroring a real Popen's .poll() contract (None while running, an int
    once it has exited)."""

    def __init__(self, *, dies_immediately=False):
        self._returncode = 0 if dies_immediately else None

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0


def _fake_spawn(argv):
    return _FakeTunnelProc()


def _dead_on_arrival_spawn(argv):
    return _FakeTunnelProc(dies_immediately=True)


class _FakeClient:
    def __init__(self, *, launch_raises=False, active_after=1, never_ip=False):
        self.launch_raises = launch_raises
        self.active_after = active_after
        self.never_ip = never_ip
        self._polls = 0
        self.launched = None
        self.terminated = []

    def launch(self, *, region, instance_type, ssh_key_names, startup_script, name):
        if self.launch_raises:
            raise RuntimeError("lambda 500")
        self.launched = {
            "region": region,
            "instance_type": instance_type,
            "ssh_key_names": ssh_key_names,
            "startup_script": startup_script,
            "name": name,
        }
        return "i-123"

    def instance(self, instance_id):
        self._polls += 1
        if self.never_ip:
            return ("booting", "")
        return ("active", "1.2.3.4") if self._polls >= self.active_after else ("booting", "")

    def terminate(self, instance_id):
        self.terminated.append(instance_id)


def _prov():
    return LambdaLiveProvisioner()


def test_available_is_true():
    ok, detail = _prov().available()
    assert ok is True and "Lambda" in detail


def test_provision_success_returns_instance_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(region="us-west-1"),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok and r.status == "provisioned"
    assert r.handle == "i-123"
    assert r.endpoint is not None
    assert r.endpoint.base_url.startswith("http://127.0.0.1:") and r.endpoint.base_url.endswith(
        "/v1"
    )
    assert r.endpoint.tunnel_remote_host == "1.2.3.4"  # the VM's real IP, kept for tunnel restart
    assert r.endpoint.tunnel_private_key_openssh  # a fresh keypair was generated
    assert client.launched["region"] == "us-west-1" and client.launched["ssh_key_names"] == ["k"]
    assert "vllm" in client.launched["startup_script"]  # the serving bootstrap is passed
    assert "127.0.0.1:8000:8000" in client.launched["startup_script"]  # loopback-only, not public
    assert "authorized_keys" in client.launched["startup_script"]  # the tunnel key is installed
    assert client.terminated == []  # success: nothing torn down


def test_provision_bakes_the_tunnel_public_key_into_the_startup_script(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok
    assert "ssh-ed25519" in client.launched["startup_script"]
    assert "permitopen=" in client.launched["startup_script"]  # port-forward only, no shell
    assert "no-pty" in client.launched["startup_script"]


def test_provision_tunnel_failure_terminates_and_does_not_fall_back_to_public_ip(monkeypatch):
    """A dead-on-arrival tunnel process must not silently fall back to the old cleartext endpoint - even
    with the insecure opt-in set, since a failed secure channel is a hard failure, not a downgrade."""
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_dead_on_arrival_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert not r.ok and r.status == "error"
    assert r.endpoint is None
    assert client.terminated == ["i-123"]  # guaranteed teardown, no cleartext endpoint left behind


def test_never_active_terminates():
    client = _FakeClient(never_ip=True)
    r = _prov().provision(
        _spec(), client=client, sleep=lambda s: None, ssh_key_names=["k"], ready_attempts=3
    )
    assert not r.ok and "active" in r.detail
    assert client.terminated == ["i-123"]  # guaranteed teardown of the billable VM


def test_launch_failure_leaves_nothing_to_terminate():
    client = _FakeClient(launch_raises=True)
    r = _prov().provision(_spec(), client=client, ssh_key_names=["k"])
    assert not r.ok and "launch failed" in r.detail
    assert client.terminated == []


def test_no_model_rejected():
    client = _FakeClient()
    r = _prov().provision(_spec(model=""), client=client, ssh_key_names=["k"])
    assert not r.ok and "model" in r.detail.lower()
    assert client.launched is None


def test_provision_succeeds_by_default_with_no_env_flags_via_the_tunnel(monkeypatch):
    """Closes the spec's gap: previously the only reachable states were refuse-by-default or an
    explicit insecure opt-in. With a working tunnel, the default (no flags at all) now succeeds AND is
    secure - ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT is not required for a working endpoint."""
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok and r.status == "provisioned"
    assert r.endpoint.base_url.startswith("http://127.0.0.1:")
    assert client.terminated == []


def test_require_secure_does_not_refuse_a_tunnel_secured_endpoint(monkeypatch):
    """ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT is a hard override for a genuinely insecure (cleartext
    public) endpoint - it must not refuse an endpoint this mechanism already secured."""
    monkeypatch.setenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", "1")
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok and r.status == "provisioned"
    assert client.terminated == []


def test_teardown_terminates():
    client = _FakeClient()
    ok, _ = _prov().teardown("i-123", client=client)
    assert ok and client.terminated == ["i-123"]


def test_teardown_also_stops_any_live_tunnel_for_the_handle():
    # Provision (with a fake spawn) so a tunnel entry exists under "lambda:i-123", then confirm
    # teardown stops it - otherwise a torn-down VM would leave a zombie ssh process retrying forever.
    tm = secure_tunnel.SSHTunnelManager()
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=tm,
    )
    assert r.ok
    assert tm.status("lambda:i-123")["running"] is True
    _prov().teardown("i-123", client=_FakeClient(), tunnel_manager=tm)
    assert tm.status("lambda:i-123")["running"] is False


def test_registry_uses_the_live_lambda_provisioner():
    assert isinstance(provision.get_provisioner("lambda"), LambdaLiveProvisioner)
    assert provision.get_provisioner("lambda").available()[0] is True


# ── single-node tensor-parallel serving (docs/specs/multi-gpu-tensor-parallel-serving.md) ───────────


@pytest.mark.parametrize(
    ("instance_type", "expected"),
    [
        ("gpu_8x_h100", 8),
        ("gpu_4x_a100", 4),
        ("gpu_1x_a10", 1),
        ("", 1),
        ("h100", 1),  # no gpu_Nx_ prefix - never mistaken for a multi-GPU SKU
    ],
)
def test_gpu_count_from_instance_type(instance_type, expected):
    assert gpu_count_from_instance_type(instance_type) == expected


def test_vllm_startup_script_appends_tensor_parallel_flag_only_when_multi_gpu():
    multi = vllm_startup_script("model", "key", gpu_count=8)
    single_explicit = vllm_startup_script("model", "key", gpu_count=1)
    single_default = vllm_startup_script("model", "key")
    assert "--tensor-parallel-size 8" in multi
    assert "--tensor-parallel-size" not in single_explicit
    assert "--tensor-parallel-size" not in single_default
    assert single_explicit == single_default  # no behavior change to the existing single-GPU path


def test_provision_with_multi_gpu_instance_type_passes_the_tensor_parallel_flag(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        instance_type="gpu_8x_h100",
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok
    assert "--tensor-parallel-size 8" in client.launched["startup_script"]
    assert "tensor-parallel across 8 GPUs" in r.detail


def test_provision_single_gpu_instance_type_omits_the_tensor_parallel_flag(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        instance_type="gpu_1x_a10",
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok
    assert "--tensor-parallel-size" not in client.launched["startup_script"]


def test_provision_refuses_a_gpu_count_that_does_not_divide_the_models_heads(monkeypatch):
    # A model with 66 attention heads cannot tensor-parallel across 8 GPUs (66 % 8 != 0). Refused
    # BEFORE client.launch() is ever called - no billable VM, nothing to tear down.
    monkeypatch.setattr(
        sizing,
        "load_catalog",
        lambda: (sizing.Model(name="qwen2.5:7b", params_b=7.0, num_attention_heads=66),),
    )
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        instance_type="gpu_8x_h100",
        ssh_key_names=["k"],
    )
    assert not r.ok and r.status == "error"
    assert "66" in r.detail and "8" in r.detail
    assert client.launched is None  # refused before launch - not merely torn down after


def test_provision_accepts_a_gpu_count_that_divides_the_models_heads(monkeypatch):
    monkeypatch.setattr(
        sizing,
        "load_catalog",
        lambda: (sizing.Model(name="qwen2.5:7b", params_b=7.0, num_attention_heads=64),),
    )
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        instance_type="gpu_8x_h100",
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok
    assert client.launched is not None


def test_provision_with_unknown_head_count_is_not_blocked(monkeypatch):
    # The model isn't in the (empty) catalog - num_attention_heads resolves to 0 (unknown), which is
    # non-blocking per gpu_count_divides_heads; vLLM's own runtime refusal is the backstop.
    monkeypatch.setattr(sizing, "load_catalog", lambda: ())
    client = _FakeClient(active_after=1)
    r = _prov().provision(
        _spec(),
        client=client,
        sleep=lambda s: None,
        instance_type="gpu_8x_h100",
        ssh_key_names=["k"],
        tunnel_spawn=_fake_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert r.ok


# ── the httpx Cloud-API client (network mocked) ───────────────────────────────────────


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._p


def test_real_client_launch_instance_terminate(monkeypatch):
    import httpx

    calls = []

    def _request(method, url, auth=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "auth": auth, "json": json})
        if url.endswith("/instance-operations/launch"):
            return _Resp({"data": {"instance_ids": ["i-9"]}})
        if "/instances/i-9" in url:
            return _Resp({"data": {"status": "active", "ip": "9.9.9.9"}})
        if url.endswith("/instance-operations/terminate"):
            return _Resp({"data": {"terminated_instances": [{"id": "i-9"}]}})
        raise AssertionError(f"unexpected {url}")

    monkeypatch.setattr(httpx, "request", _request)
    c = _RealLambdaClient("lam-key")
    iid = c.launch(
        region="us-west-1",
        instance_type="gpu_1x_a10",
        ssh_key_names=["k"],
        startup_script="#!/bin/sh",
        name="x",
    )
    assert iid == "i-9"
    assert c.instance("i-9") == ("active", "9.9.9.9")
    c.terminate("i-9")  # must not raise
    assert all(call["auth"] == ("lam-key", "") for call in calls)  # Basic auth, key as username
    assert any("cloud.lambdalabs.com/api/v1" in call["url"] for call in calls)


def test_real_client_api_error_raises(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **k: _Resp({"error": {"message": "bad key"}}, status=401),
    )
    with pytest.raises(provision.ProvisionError):
        _RealLambdaClient("k").launch(
            region="r", instance_type="t", ssh_key_names=[], startup_script="", name="n"
        )


def test_real_client_needs_a_key():
    with pytest.raises(provision.ProvisionError):
        _RealLambdaClient("")
