"""Provider-agnostic provisioning (Direction A): the planner registry for the org serving plane.

Real cloud provisioning needs a live account and ships later; for now every provider is a planner
that can describe what it would do (``plan``) and reports that provisioning is not yet wired up
(``available`` False, ``provision`` raises ``ProvisionerNotReady``). These tests pin that contract.
"""

import pytest

from anthill.hosting import provision
from anthill.hosting.endpoint import OrgEndpoint
from anthill.hosting.provision import (
    ProvisionerNotReady,
    ProvisionError,
    ProvisionSpec,
    endpoint_is_secure,
)


def _spec(provider: str, **kw) -> ProvisionSpec:
    base = {"model": "qwen2.5:32b", "params_b": 32.0, "region": "us-east-1"}
    base.update(kw)
    return ProvisionSpec(provider=provider, **base)


# ── registry ──────────────────────────────────────────────────────────────────────


def test_registry_covers_every_provider_key():
    seen = {p.key for p in provision.all_provisioners()}
    assert seen == set(provision.PROVIDER_KEYS)
    # picker order is preserved
    assert [p.key for p in provision.all_provisioners()] == list(provision.PROVIDER_KEYS)


def test_get_provisioner_normalizes_and_rejects_unknown():
    assert provision.get_provisioner("AWS").key == "aws"  # case-insensitive
    assert provision.get_provisioner(" runpod ").key == "runpod"  # trimmed
    with pytest.raises(ProvisionError):
        provision.get_provisioner("openai")  # not a self-hosting provider
    with pytest.raises(ProvisionError):
        provision.get_provisioner("modal")  # Modal is not a hosting provider (serverless, no wiki)


def test_tier_mapping():
    assert provision.tier_of("onprem") == "onprem"
    assert provision.tier_of("aws") == "vpc" and provision.tier_of("lambda") == "vpc"
    assert provision.tier_of("ovh") == "vpc" and provision.tier_of("runpod") == "neocloud"
    with pytest.raises(ProvisionError):
        provision.tier_of("nope")


def test_providers_for_tier():
    assert provision.providers_for_tier("onprem") == ("onprem",)
    # Lambda is the primary (first) Cloud VPC option; OVH/Scaleway/DataCrunch/Nebius are EU-sovereign
    assert provision.providers_for_tier("vpc") == (
        "lambda",
        "aws",
        "gcp",
        "azure",
        "ibm",
        "ovh",
        "scaleway",
        "datacrunch",
        "nebius",
    )
    assert provision.providers_for_tier("neocloud") == ("runpod",)  # Modal removed


def test_eu_sovereign_flagged_on_ovh_and_scaleway():
    assert provision.get_provisioner("ovh").eu_sovereign is True
    assert provision.get_provisioner("scaleway").eu_sovereign is True
    assert provision.get_provisioner("aws").eu_sovereign is False
    assert provision.get_provisioner("lambda").eu_sovereign is False


# ── planner contract (pure, no side effects) ────────────────────────────────────────


def test_onprem_plan_uses_ollama_and_pulls_the_model():
    plan = provision.get_provisioner("onprem").plan(_spec("onprem", region=""))
    assert plan.tier == "onprem" and plan.serving_stack == "ollama" and not plan.cold_start
    names = [s.name for s in plan.steps]
    assert names[0] == "check_host" and names[-1] == "validate"
    pull = next(s for s in plan.steps if s.name == "pull_model")
    assert "qwen2.5:32b" in pull.detail


def test_vpc_plan_launches_a_gpu_instance_with_vllm():
    plan = provision.get_provisioner("gcp").plan(_spec("gcp"))
    assert plan.tier == "vpc" and plan.serving_stack == "vllm" and not plan.cold_start
    launch = next(s for s in plan.steps if s.name == "launch_instance")
    assert "us-east-1" in launch.detail  # the chosen region is surfaced
    auth = next(s for s in plan.steps if s.name == "authenticate")
    assert "GCP" in auth.detail


def test_vpc_plan_honours_explicit_instance_type_else_sizes_from_params():
    sized = provision.get_provisioner("aws").plan(_spec("aws", instance_type=""))
    launch = next(s for s in sized.steps if s.name == "launch_instance")
    assert "32B" in launch.detail  # sized from params_b
    explicit = provision.get_provisioner("aws").plan(_spec("aws", instance_type="g5.2xlarge"))
    launch2 = next(s for s in explicit.steps if s.name == "launch_instance")
    assert "g5.2xlarge" in launch2.detail


def test_neocloud_plan_is_serverless_with_cold_start_and_caveat():
    plan = provision.get_provisioner("runpod").plan(_spec("runpod", region=""))
    assert plan.tier == "neocloud" and plan.serving_stack == "serverless" and plan.cold_start
    assert "scale_policy" in [s.name for s in plan.steps]
    assert "shared GPUs" in plan.notes  # the amber caveat is carried through


def test_serve_wiki_toggle_adds_or_drops_the_wiki_host_step():
    with_wiki = provision.get_provisioner("aws").plan(_spec("aws", serve_wiki=True))
    without = provision.get_provisioner("aws").plan(_spec("aws", serve_wiki=False))
    assert "wiki_host" in [s.name for s in with_wiki.steps]
    assert "wiki_host" not in [s.name for s in without.steps]


def test_plan_rejects_a_mismatched_provider_in_the_spec():
    with pytest.raises(ProvisionError):
        provision.get_provisioner("aws").plan(_spec("gcp"))  # provisioner != spec.provider


def test_every_plan_ends_in_validation():
    for key in provision.PROVIDER_KEYS:
        plan = provision.get_provisioner(key).plan(_spec(key))
        assert plan.steps[-1].name == "validate"


# ── not-ready contract ──────────────────────────────────────────────────────────────


_LIVE = {"runpod", "lambda", "datacrunch"}  # have real provisioners; the rest are planner stubs


def test_stub_providers_are_not_wired_up_yet():
    for p in provision.all_provisioners():
        if p.key in _LIVE:
            continue
        ok, detail = p.available()
        assert ok is False
        assert "manual" in detail  # points at the escape hatch
        assert detail.strip()


def test_live_providers_are_wired_up():
    assert provision.get_provisioner("runpod").available()[0] is True
    assert provision.get_provisioner("lambda").available()[0] is True


def test_stub_provision_and_teardown_raise_not_ready():
    p = provision.get_provisioner("aws")  # a still-stubbed provider
    with pytest.raises(ProvisionerNotReady):
        p.provision(_spec("aws"))
    with pytest.raises(ProvisionerNotReady):
        p.teardown("deploy-123")


def test_not_ready_is_a_provision_error_subclass():
    # callers can catch the base class to handle both "unknown" and "not ready" uniformly
    assert issubclass(ProvisionerNotReady, ProvisionError)


# ── endpoint_is_secure() (docs/specs/llm-endpoint-secure-transport.md) ─────────────────


def test_endpoint_is_secure_accepts_https_unchanged():
    assert endpoint_is_secure(OrgEndpoint(base_url="https://example.com/v1")) is True


def test_endpoint_is_secure_accepts_a_loopback_tunnel_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    assert endpoint_is_secure(OrgEndpoint(base_url="http://127.0.0.1:54321/v1")) is True
    assert endpoint_is_secure(OrgEndpoint(base_url="http://localhost:54321/v1")) is True
    assert endpoint_is_secure(OrgEndpoint(base_url="http://LOCALHOST:54321/v1")) is True  # case


def test_endpoint_is_secure_rejects_a_lookalike_loopback_host(monkeypatch):
    # http://127.0.0.1.evil.example must not be treated as the real loopback host.
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    ep = OrgEndpoint(base_url="http://127.0.0.1.evil.example/v1")
    assert endpoint_is_secure(ep) is False


def test_endpoint_is_secure_still_refuses_public_http_by_default(monkeypatch):
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    assert endpoint_is_secure(OrgEndpoint(base_url="http://1.2.3.4:8000/v1")) is False


def test_endpoint_is_secure_public_http_allowed_only_with_explicit_optin(monkeypatch):
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    assert endpoint_is_secure(OrgEndpoint(base_url="http://1.2.3.4:8000/v1")) is True


def test_endpoint_is_secure_require_secure_overrides_the_insecure_optin_for_public_http(
    monkeypatch,
):
    monkeypatch.setenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", "1")
    monkeypatch.setenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", "1")
    assert endpoint_is_secure(OrgEndpoint(base_url="http://1.2.3.4:8000/v1")) is False


def test_endpoint_is_secure_require_secure_does_not_refuse_a_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", "1")
    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    assert endpoint_is_secure(OrgEndpoint(base_url="http://127.0.0.1:9000/v1")) is True
