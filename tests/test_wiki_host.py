"""The always-on wiki/backend host provisioner (anthill/hosting/wiki_host.py) and its wiring into
provision_org / teardown_org: when an admin provisions the model on RunPod with serve_wiki on, Anthill
also stands up a CPU backend pod and records it; teardown tears both down. Injected fake clients - no
real cloud calls, no spend."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting.endpoint import Check, Validation
from anthill.hosting.runpod_provision import RunpodDeploy
from anthill.hosting.wiki_host import WikiHostError, provision_wiki_host, teardown_wiki_host
from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings
from anthill.web.provision_run import provision_org, teardown_org

_OK = Validation(True, [Check("round_trip", True, "ok")])


class _FakeModelClient:
    """The serverless model endpoint (RunPod), mocked - same surface as the real provisioner expects."""

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
        return RunpodDeploy(endpoint_id="ep9", base_url="https://api.runpod.ai/v2/ep9/openai/v1")

    def endpoint_ready(self, endpoint_id):
        return True

    def delete_endpoint(self, endpoint_id):
        pass


class _FakeWikiClient:
    def __init__(self, *, ready_after=1, fail_launch=False):
        self.ready_after = ready_after
        self.fail_launch = fail_launch
        self._polls = 0
        self.launched = None
        self.terminated = []

    def launch(self, *, name, image, disk_gb):
        if self.fail_launch:
            raise RuntimeError("boom")
        self.launched = {"name": name, "image": image, "disk_gb": disk_gb}
        return "pod-1"

    def public_url(self, pod_id):
        self._polls += 1
        return (
            f"https://{pod_id}-8000.proxy.runpod.net" if self._polls >= self.ready_after else None
        )

    def terminate(self, pod_id):
        self.terminated.append(pod_id)


# ── unit: provision_wiki_host / teardown_wiki_host ──────────────────────────────


def test_provision_wiki_host_returns_id_and_url():
    c = _FakeWikiClient(ready_after=2)
    pod_id, url = provision_wiki_host(name="anthill-wiki-1", client=c, sleep=lambda s: None)
    assert pod_id == "pod-1" and url == "https://pod-1-8000.proxy.runpod.net"
    assert c.launched["name"] == "anthill-wiki-1" and not c.terminated  # came up, no teardown


def test_provision_wiki_host_times_out_and_tears_down():
    c = _FakeWikiClient(ready_after=999)  # never ready
    with pytest.raises(WikiHostError):
        provision_wiki_host(name="x", client=c, ready_attempts=3, sleep=lambda s: None)
    assert c.terminated == ["pod-1"]  # guaranteed teardown of the orphan


def test_provision_wiki_host_launch_failure_raises():
    c = _FakeWikiClient(fail_launch=True)
    with pytest.raises(WikiHostError):
        provision_wiki_host(name="x", client=c, sleep=lambda s: None)


def test_teardown_wiki_host_terminates_and_ignores_blank():
    c = _FakeWikiClient()
    teardown_wiki_host("pod-9", client=c)
    assert c.terminated == ["pod-9"]
    teardown_wiki_host("", client=c)  # blank is a no-op
    assert c.terminated == ["pod-9"]


# ── wiring into provision_org / teardown_org ────────────────────────────────────


def _eng(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def _seed(eng, **cfg_kw):
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    base = {
        "org_provider": "runpod",
        "org_model": "qwen2.5:7b",
        "org_model_params": "7",
        "org_serve_wiki": True,
    }
    base.update(cfg_kw)
    s.add(OrgSettings(org_id=org.id, **base))
    s.commit()
    return org.id


def _cfg(eng, org_id):
    return sessionmaker(bind=eng)().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


def test_provision_org_brings_up_and_records_wiki_host(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng)
    wc = _FakeWikiClient()
    status = provision_org(
        eng,
        org_id,
        client=_FakeModelClient(),
        wiki_client=wc,
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    assert status == "provisioned"
    cfg = _cfg(eng, org_id)
    assert cfg.org_wiki_pod_handle == "pod-1"
    assert cfg.wiki_vpc_url == "https://pod-1-8000.proxy.runpod.net" and cfg.wiki_hosting == "vpc"
    assert wc.launched["image"]  # an image was passed to the pod


def test_serve_wiki_off_skips_the_host(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, org_serve_wiki=False)
    wc = _FakeWikiClient()
    provision_org(
        eng,
        org_id,
        client=_FakeModelClient(),
        wiki_client=wc,
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    cfg = _cfg(eng, org_id)
    assert cfg.org_wiki_pod_handle == "" and not wc.launched  # not provisioned


def test_wiki_host_failure_keeps_model_provisioned(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng)
    wc = _FakeWikiClient(fail_launch=True)
    status = provision_org(
        eng,
        org_id,
        client=_FakeModelClient(),
        wiki_client=wc,
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    assert status == "provisioned"  # the model is up; the wiki host is best-effort
    cfg = _cfg(eng, org_id)
    assert cfg.org_wiki_pod_handle == "" and "Wiki host not ready" in cfg.org_backend_detail


def test_teardown_tears_down_the_wiki_pod(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng)
    wc = _FakeWikiClient()
    provision_org(
        eng,
        org_id,
        client=_FakeModelClient(),
        wiki_client=wc,
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    teardown_org(eng, org_id, client=_FakeModelClient(), wiki_client=wc)
    cfg = _cfg(eng, org_id)
    assert wc.terminated == ["pod-1"] and cfg.org_wiki_pod_handle == ""


# ── the production auto-provision is gated off until launch ──────────────────────


def test_production_autohost_is_gated_off_by_default(monkeypatch):
    """The live path (no injected client) must NOT bring up a backend pod until the feature is enabled -
    so an alpha user provisioning a model never hits an empty/unreachable backend pod."""
    from types import SimpleNamespace

    from anthill.web.provision_run import _provision_wiki_host_if_requested

    monkeypatch.delenv("ANTHILL_WIKI_HOST_AUTOPROVISION", raising=False)
    cfg = SimpleNamespace(
        org_provider="runpod",
        org_id=1,
        org_backend_detail="",
        org_provision_key_enc="",
        org_wiki_pod_handle="",
        wiki_vpc_url="",
        wiki_hosting="local",
    )
    spec = SimpleNamespace(serve_wiki=True)
    # build=True (production), no injected wiki client, flag off -> skipped before any client is built
    _provision_wiki_host_if_requested(
        None, cfg, spec, wiki_client=None, build=True, sleep=lambda s: None
    )
    # no pod is brought up, and the note says it activates at launch / runs on this device for now
    detail = cfg.org_backend_detail.lower()
    assert cfg.org_wiki_pod_handle == "" and "at launch" in detail and "this device" in detail


def test_flag_on_attempts_the_live_path(monkeypatch):
    from types import SimpleNamespace

    from anthill.web.provision_run import _provision_wiki_host_if_requested

    monkeypatch.setenv("ANTHILL_WIKI_HOST_AUTOPROVISION", "1")
    cfg = SimpleNamespace(
        org_provider="runpod",
        org_id=1,
        org_backend_detail="",
        org_provision_key_enc="",  # no key -> the live client raises -> recorded as not ready (no crash)
        org_wiki_pod_handle="",
        wiki_vpc_url="",
        wiki_hosting="local",
    )
    spec = SimpleNamespace(serve_wiki=True)
    _provision_wiki_host_if_requested(
        None, cfg, spec, wiki_client=None, build=True, sleep=lambda s: None
    )
    assert cfg.org_wiki_pod_handle == "" and "not ready" in cfg.org_backend_detail.lower()
