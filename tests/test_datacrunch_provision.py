"""Live DataCrunch/Verda provisioner (the network is mocked): launch -> poll-running -> endpoint, with
a guaranteed terminate on any post-launch failure so no billable VM is left behind."""

import pytest

from anthill.hosting import provision
from anthill.hosting.datacrunch_provision import DataCrunchLiveProvisioner


def _spec(**kw):
    base = {"provider": "datacrunch", "model": "meta-llama/Llama-3.1-8B", "params_b": 8.0}
    base.update(kw)
    return provision.ProvisionSpec(**base)


class _FakeClient:
    def __init__(self, *, launch_raises=False, statuses=None):
        self.launch_raises = launch_raises
        self.statuses = list(statuses or [("running", "10.0.0.5")])
        self.authenticated = 0
        self.launched = None
        self.terminated = []

    def authenticate(self):
        self.authenticated += 1

    def launch(self, *, instance_type, location_code, image, hostname, ssh_key_ids, description):
        if self.launch_raises:
            raise RuntimeError("datacrunch 500")
        self.launched = {
            "instance_type": instance_type,
            "location_code": location_code,
            "image": image,
            "hostname": hostname,
            "ssh_key_ids": ssh_key_ids,
            "description": description,
        }
        return "inst-123"

    def instance(self, instance_id):
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def terminate(self, instance_id):
        self.terminated.append(instance_id)


def _no_sleep(_seconds):
    return None


def _prov():
    return DataCrunchLiveProvisioner()


def test_available_is_true():
    ok, detail = _prov().available()
    assert ok is True and "DataCrunch" in detail


def test_provision_failed_launch_no_teardown():
    c = _FakeClient(launch_raises=True)
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep)
    assert not r.ok and r.status == "error"
    assert c.launched is None
    assert c.terminated == []  # nothing was created, nothing torn down
    assert r.handle == ""


def test_provision_poll_failure_triggers_teardown():
    c = _FakeClient(statuses=[("provisioning", "")])
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep, ready_attempts=3)
    assert not r.ok and r.status == "error"
    assert c.terminated == ["inst-123"]  # guaranteed teardown
    assert r.handle == ""


def test_provision_offline_status_triggers_teardown():
    c = _FakeClient(statuses=[("offline", "")])
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep, ready_attempts=3)
    assert not r.ok and r.status == "error"
    assert c.terminated == ["inst-123"]


def test_provision_insecure_endpoint_refused_and_torn_down(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    c = _FakeClient(statuses=[("running", "203.0.113.9")])  # public IP, http -> insecure
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep)
    assert not r.ok and r.status == "error"
    assert "refused" in r.detail
    assert c.terminated == ["inst-123"]


def test_provision_insecure_endpoint_allowed_with_override(monkeypatch):
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    c = _FakeClient(statuses=[("running", "203.0.113.9")])
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep)
    assert r.ok and r.status == "provisioned"
    assert c.terminated == []
    assert r.endpoint is not None and r.handle == "inst-123"


def test_provision_require_secure_overrides_allow_insecure(monkeypatch):
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    monkeypatch.setenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", "1")
    c = _FakeClient(statuses=[("running", "203.0.113.9")])
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep)
    assert not r.ok and r.status == "error"
    assert c.terminated == ["inst-123"]


def test_provision_success_returns_instance_endpoint(monkeypatch):
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    c = _FakeClient(statuses=[("provisioning", ""), ("running", "10.0.0.5")])
    r = _prov().provision(_spec(), client=c, sleep=_no_sleep)
    assert r.ok and r.status == "provisioned"
    ep = r.endpoint
    assert ep.model == "meta-llama/Llama-3.1-8B"
    assert ep.base_url == "http://10.0.0.5:8000/v1"
    assert ep.api_key and len(ep.api_key) >= 32  # random secret
    assert r.handle == "inst-123"
    assert c.launched["location_code"] == "FIN-01"  # default EU-sovereign location


def test_provision_uses_spec_region_over_default(monkeypatch):
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    c = _FakeClient(statuses=[("running", "10.0.0.5")])
    r = _prov().provision(_spec(region="ICE-01"), client=c, sleep=_no_sleep)
    assert r.ok
    assert c.launched["location_code"] == "ICE-01"


def test_provision_no_model_selected():
    r = _prov().provision(_spec(model=""), client=_FakeClient(), sleep=_no_sleep)
    assert not r.ok and r.status == "error"
    assert "No model selected" in r.detail


def test_provision_wrong_provider_raises():
    c = _FakeClient()
    with pytest.raises(provision.ProvisionError):
        _prov().provision(provision.ProvisionSpec(provider="lambda", model="x"), client=c)


def test_teardown_empty_handle():
    ok, msg = _prov().teardown("")
    assert ok and "nothing" in msg


def test_teardown_terminates():
    c = _FakeClient()
    ok, _msg = _prov().teardown("inst-9", client=c)
    assert ok and c.terminated == ["inst-9"]


def test_registered():
    assert "datacrunch" in provision.PROVIDER_KEYS
    assert provision._TIER_OF["datacrunch"] == "vpc"
    assert provision._registry()["datacrunch"] is DataCrunchLiveProvisioner


def test_planner_metadata():
    p = provision.DataCrunchPlanner()
    assert p.key == "datacrunch"
    assert p.name == "DataCrunch / Verda"
    assert p.tier == "vpc" and p.eu_sovereign is True
