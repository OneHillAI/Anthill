"""Live RunPod provisioner orchestration (the network is mocked): create -> poll-to-registered, with a
guaranteed teardown on any post-create failure so nothing billable is ever left behind. Also covers the
httpx GraphQL client (no `runpod` SDK import)."""

import sys

import pytest

from anthill.hosting import provision
from anthill.hosting.endpoint import Check, Validation
from anthill.hosting.runpod_provision import (
    _DEFAULT_RUNPOD_VLLM_IMAGE,
    RunpodDeploy,
    RunpodLiveProvisioner,
    _RealRunpodClient,
    _vllm_image,
)


def _spec(**kw):
    base = {"provider": "runpod", "model": "qwen2.5:7b", "params_b": 7.0}
    base.update(kw)
    return provision.ProvisionSpec(**base)


class _FakeClient:
    """Records calls; configurable to fail at create or never become ready."""

    def __init__(self, *, create_raises=False, ready_after=1):
        self.create_raises = create_raises
        self.ready_after = ready_after  # become ready on the Nth poll
        self._polls = 0
        self.created = None
        self.deleted = []

    def create_serverless_endpoint(
        self,
        *,
        name,
        hf_model,
        max_workers,
        idle_seconds,
        gpu_ids="AMPERE_24",
        hf_token="",
        min_workers=0,
    ):
        if self.create_raises:
            raise RuntimeError("runpod 500")
        self.created = {
            "name": name,
            "hf_model": hf_model,
            "max_workers": max_workers,
            "idle_seconds": idle_seconds,
            "gpu_ids": gpu_ids,
            "hf_token": hf_token,
            "min_workers": min_workers,
        }
        return RunpodDeploy(
            endpoint_id="ep123", base_url="https://api.runpod.ai/v2/ep123/openai/v1"
        )

    def endpoint_ready(self, endpoint_id):
        self._polls += 1
        return self._polls >= self.ready_after

    def delete_endpoint(self, endpoint_id):
        self.deleted.append(endpoint_id)


_OK = Validation(True, [Check("round_trip", True, "ok")])
_FAIL = Validation(False, [Check("reachable", False, "connection refused")])


def _prov():
    return RunpodLiveProvisioner()


def test_available_is_true():
    ok, detail = _prov().available()
    assert ok is True and "RunPod" in detail


def test_provision_success_returns_validated_endpoint():
    client = _FakeClient(ready_after=1)
    r = _prov().provision(_spec(), client=client, validate=lambda ep: _OK, sleep=lambda s: None)
    assert r.ok and r.status == "provisioned"
    assert r.handle == "ep123"
    assert r.endpoint and r.endpoint.base_url.endswith("/ep123/openai/v1")
    assert r.endpoint.model == "qwen2.5:7b"
    assert client.created["hf_model"] == "qwen2.5:7b"
    assert client.created["max_workers"] == 1 and client.created["idle_seconds"] == 30  # cost cap
    assert client.deleted == []  # success: nothing torn down


def test_provision_passes_gpu_ids_to_the_client():
    client = _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=client, sleep=lambda s: None, gpu_ids="AMPERE_80")
    assert client.created["gpu_ids"] == "AMPERE_80"
    # default when none is chosen
    client2 = _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=client2, sleep=lambda s: None)
    assert client2.created["gpu_ids"] == "AMPERE_24"


def test_provision_passes_hf_token_to_the_client():
    client = _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=client, sleep=lambda s: None, hf_token="hf_secret")
    assert client.created["hf_token"] == "hf_secret"


def test_provision_name_is_unique_per_attempt():
    # RunPod requires unique template names; a retry must not collide with a leftover template
    c1, c2 = _FakeClient(ready_after=1), _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=c1, sleep=lambda s: None)
    _prov().provision(_spec(), client=c2, sleep=lambda s: None)
    n1, n2 = c1.created["name"], c2.created["name"]
    assert n1 != n2  # distinct each time
    assert n1.startswith("anthill-") and len(n1) <= 60


def test_provision_name_suffix_is_injectable():
    client = _FakeClient(ready_after=1)
    _prov().provision(
        _spec(model="qwen2.5:7b"), client=client, sleep=lambda s: None, name_suffix="abcd1234"
    )
    assert client.created["name"] == "anthill-qwen2.5-7b-abcd1234"


def test_validation_failure_tears_down():
    client = _FakeClient(ready_after=1)
    r = _prov().provision(_spec(), client=client, validate=lambda ep: _FAIL, sleep=lambda s: None)
    assert not r.ok and r.status == "error"
    assert "connection refused" in r.detail
    assert client.deleted == ["ep123"]  # guaranteed teardown of the billable endpoint


def test_never_ready_tears_down():
    client = _FakeClient(ready_after=999)  # never reaches ready within the attempts
    r = _prov().provision(
        _spec(), client=client, validate=lambda ep: _OK, sleep=lambda s: None, ready_attempts=3
    )
    assert not r.ok and "register" in r.detail
    assert client.deleted == ["ep123"]  # torn down even though it never served


def test_create_failure_leaves_nothing_to_tear_down():
    client = _FakeClient(create_raises=True)
    r = _prov().provision(_spec(), client=client, validate=lambda ep: _OK, sleep=lambda s: None)
    assert not r.ok and "create failed" in r.detail
    assert client.deleted == []  # nothing was created


def test_no_model_is_rejected():
    client = _FakeClient()
    r = _prov().provision(_spec(model=""), client=client, validate=lambda ep: _OK)
    assert not r.ok and "model" in r.detail.lower()
    assert client.created is None


def test_teardown_deletes_the_endpoint():
    client = _FakeClient()
    ok, _ = _prov().teardown("ep123", client=client)
    assert ok and client.deleted == ["ep123"]


def test_teardown_noop_on_blank_handle():
    client = _FakeClient()
    ok, _ = _prov().teardown("  ", client=client)
    assert ok and client.deleted == []


def test_provision_still_plans():
    # the inherited planner is intact (plan() works without a client)
    plan = _prov().plan(_spec(region=""))
    assert plan.tier == "neocloud" and plan.serving_stack == "serverless" and plan.cold_start
    assert plan.steps[-1].name == "validate"


def test_provider_mismatch_rejected():
    with pytest.raises(provision.ProvisionError):
        _prov().provision(_spec(provider="aws"), client=_FakeClient())


# ── the httpx GraphQL client (network mocked; no runpod SDK) ───────────────────────────


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._p


def _patch_httpx(monkeypatch, responder):
    import httpx

    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "query": (json or {}).get("query", "")})
        return responder(len(calls), (json or {}).get("query", ""))

    monkeypatch.setattr(httpx, "post", _post)
    return calls


def test_real_client_uses_httpx_graphql_not_the_sdk(monkeypatch):
    # the runpod SDK must NOT be imported (we removed that heavy dependency)
    monkeypatch.setitem(sys.modules, "runpod", None)

    def responder(n, query):
        if "saveTemplate" in query:
            # the deployed image must be a real, pinned tag - never `:stable` (404s on the registry)
            assert "runpod/worker-v1-vllm:v" in query and ":stable" not in query
            return _Resp({"data": {"saveTemplate": {"id": "tpl1"}}})
        if "saveEndpoint" in query:
            assert 'templateId: "tpl1"' in query and "workersMin: 0" in query
            assert 'gpuIds: "AMPERE_24"' in query  # the default GPU pool is carried through
            return _Resp({"data": {"saveEndpoint": {"id": "epABC"}}})
        raise AssertionError("unexpected query")

    calls = _patch_httpx(monkeypatch, responder)
    dep = _RealRunpodClient("rp-key").create_serverless_endpoint(
        name="anthill-m", hf_model="qwen2.5:7b", max_workers=2, idle_seconds=30
    )
    assert dep.endpoint_id == "epABC"
    assert dep.base_url == "https://api.runpod.ai/v2/epABC/openai/v1"
    assert all(c["headers"]["Authorization"] == "Bearer rp-key" for c in calls)
    assert "runpod.io/graphql" in calls[0]["url"]


# --- PR #661 Tier 3: min_workers (warm pool) --------------------------------------------------------


def test_real_client_passes_nonzero_min_workers_as_workers_min(monkeypatch):
    def responder(n, query):
        if "saveTemplate" in query:
            return _Resp({"data": {"saveTemplate": {"id": "tpl1"}}})
        if "saveEndpoint" in query:
            assert "workersMin: 3" in query
            return _Resp({"data": {"saveEndpoint": {"id": "epABC"}}})
        raise AssertionError("unexpected query")

    _patch_httpx(monkeypatch, responder)
    dep = _RealRunpodClient("rp-key").create_serverless_endpoint(
        name="anthill-m", hf_model="qwen2.5:7b", max_workers=2, idle_seconds=30, min_workers=3
    )
    assert dep.endpoint_id == "epABC"


def test_real_client_defaults_min_workers_to_zero(monkeypatch):
    def responder(n, query):
        if "saveTemplate" in query:
            return _Resp({"data": {"saveTemplate": {"id": "tpl1"}}})
        if "saveEndpoint" in query:
            assert "workersMin: 0" in query  # unchanged default - scale fully to zero
            return _Resp({"data": {"saveEndpoint": {"id": "epABC"}}})
        raise AssertionError("unexpected query")

    _patch_httpx(monkeypatch, responder)
    _RealRunpodClient("rp-key").create_serverless_endpoint(
        name="anthill-m", hf_model="qwen2.5:7b", max_workers=2, idle_seconds=30
    )


def test_provision_passes_min_workers_through_to_the_client():
    # RunpodLiveProvisioner.provision() -> client.create_serverless_endpoint(): verified end to end
    # via the _FakeClient double, mirroring how every other launch kwarg (gpu_ids, hf_token) is tested.
    client = _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=client, sleep=lambda s: None, min_workers=5)
    assert client.created["min_workers"] == 5


def test_provision_defaults_min_workers_to_zero_unchanged():
    client = _FakeClient(ready_after=1)
    _prov().provision(_spec(), client=client, sleep=lambda s: None)
    assert client.created["min_workers"] == 0


def test_real_client_adds_hf_token_env_only_when_given(monkeypatch):
    def responder(n, query):
        if "saveTemplate" in query:
            responder.tpl = query
            return _Resp({"data": {"saveTemplate": {"id": "tpl1"}}})
        if "saveEndpoint" in query:
            return _Resp({"data": {"saveEndpoint": {"id": "epX"}}})
        raise AssertionError("unexpected")

    _patch_httpx(monkeypatch, responder)
    c = _RealRunpodClient("k")
    # no token -> no HF_TOKEN env (open models need none)
    c.create_serverless_endpoint(name="n", hf_model="m", max_workers=1, idle_seconds=30)
    assert "HF_TOKEN" not in responder.tpl
    # a token -> HF_TOKEN (+ the hub var vLLM also reads) carrying the value, so gated repos can pull
    c.create_serverless_endpoint(
        name="n", hf_model="m", max_workers=1, idle_seconds=30, hf_token="hf_abc"
    )
    assert 'key: "HF_TOKEN", value: "hf_abc"' in responder.tpl
    assert "HUGGING_FACE_HUB_TOKEN" in responder.tpl


def test_real_client_graphql_error_raises_provision_error(monkeypatch):
    _patch_httpx(monkeypatch, lambda n, q: _Resp({"errors": [{"message": "bad key"}]}))
    with pytest.raises(provision.ProvisionError, match="bad key"):
        _RealRunpodClient("k").create_serverless_endpoint(
            name="x", hf_model="m", max_workers=1, idle_seconds=30
        )


def test_real_client_ready_and_delete(monkeypatch):
    def responder(n, query):
        if "myself" in query:
            return _Resp({"data": {"myself": {"endpoints": [{"id": "epABC"}]}}})
        if "deleteEndpoint" in query:
            assert 'deleteEndpoint(id: "epABC")' in query
            return _Resp({"data": {"deleteEndpoint": None}})
        raise AssertionError("unexpected")

    _patch_httpx(monkeypatch, responder)
    c = _RealRunpodClient("k")
    assert c.endpoint_ready("epABC") is True
    assert c.endpoint_ready("nope") is False
    c.delete_endpoint("epABC")  # must not raise


def test_real_client_needs_a_key():
    with pytest.raises(provision.ProvisionError):
        _RealRunpodClient("")


def test_vllm_image_is_pinned_not_stable_and_env_overridable(monkeypatch):
    # RunPod publishes no `latest`/`stable` tag, so the default must be a pinned version tag.
    monkeypatch.delenv("RUNPOD_VLLM_IMAGE", raising=False)
    assert _vllm_image() == _DEFAULT_RUNPOD_VLLM_IMAGE
    assert _DEFAULT_RUNPOD_VLLM_IMAGE.startswith("runpod/worker-v1-vllm:v")
    assert ":stable" not in _DEFAULT_RUNPOD_VLLM_IMAGE
    # an operator can override to a newer tag without a rebuild
    monkeypatch.setenv("RUNPOD_VLLM_IMAGE", "runpod/worker-v1-vllm:v9.9.9")
    assert _vllm_image() == "runpod/worker-v1-vllm:v9.9.9"
    monkeypatch.setenv("RUNPOD_VLLM_IMAGE", "   ")  # blank falls back to the default
    assert _vllm_image() == _DEFAULT_RUNPOD_VLLM_IMAGE
