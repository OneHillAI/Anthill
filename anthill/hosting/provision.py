"""Provision the org serving plane (Direction A).

Anthill stands up the org's *own* inference backend rather than asking an admin to hand-build
infrastructure and paste a URL: it drives a provider's SDK - or an on-prem installer - to bring up
the serving stack, deploy the chosen open model, expose one standard chat endpoint, bring up the
always-on wiki host, and then validate the result end to end (see ``hosting/endpoint.validate``).
The org always runs on infrastructure the organization owns; Onehill hosts nothing.

This mirrors the training-backend pattern (``anthill/training/backends``): one provider-agnostic
interface, a registry, and a class per provider. Real cloud provisioning needs a live account and
ships incrementally, so every provider here is currently a *planner*: it can describe exactly what it
would do (``plan``) and report that real provisioning is not wired up yet (``available``), while
``provision`` raises ``ProvisionerNotReady``. The planner surface is real value now - the
Organization settings screen shows the admin the concrete steps before anything runs - and the manual
"connect an endpoint I run myself" path stays as the escape hatch until the live impls land.

Provider -> tier:
  onprem                      -> on-prem        (Ollama, or vLLM on a GPU box)
  aws / gcp / azure / ibm     -> cloud VPC      (vLLM / TGI on a GPU instance in your VPC)
  modal / runpod              -> neocloud       (serverless GPU deploy in your own account)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .endpoint import OrgEndpoint
from .tiers import tier as _tier

# Hosts that never leave the machine: a base_url pointing here reaches the model only through a
# supervised SSH tunnel (docs/specs/llm-endpoint-secure-transport.md, Option A) - the wire is never
# cleartext on any network, so it is secure despite the http:// scheme. Matched by exact hostname (via
# urlsplit, not a substring check) so a lookalike like http://127.0.0.1.evil.example cannot spoof it.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}

# Canonical provider keys, in the order the picker shows them, grouped by tier. Lambda Labs is the
# primary Cloud VPC option; OVHcloud + Scaleway are EU-sovereign. (Modal is serverless-only - no
# always-on host for the wiki - so it is not a hosting provider; it remains a training backend.)
PROVIDER_KEYS = (
    "onprem",
    "lambda",
    "aws",
    "gcp",
    "azure",
    "ibm",
    "ovh",
    "scaleway",
    "runpod",
    "datacrunch",
    "nebius",
)

_TIER_OF: dict[str, str] = {
    "onprem": "onprem",
    "lambda": "vpc",
    "aws": "vpc",
    "gcp": "vpc",
    "azure": "vpc",
    "ibm": "vpc",
    "ovh": "vpc",
    "scaleway": "vpc",
    "runpod": "neocloud",
    "datacrunch": "vpc",
    "nebius": "vpc",
}


class ProvisionError(RuntimeError):
    """A provisioner refused or failed (bad spec, guardrail, or provider error)."""


class ProvisionerNotReady(ProvisionError):
    """Real provisioning for this provider is not wired up yet (needs a live account).

    Raised by ``provision``/``teardown`` on the planner stubs so a caller fails loudly instead
    of silently doing nothing; the caller shows the ``plan`` and the manual-connect escape hatch.
    """


@dataclass(frozen=True)
class ProvisionSpec:
    """What the admin chose: where to run and which model to serve."""

    provider: str  # one of PROVIDER_KEYS
    model: str  # e.g. "qwen2.5:32b" or a Hugging Face id
    params_b: float = 0.0  # model size in billions (for instance sizing / honesty)
    region: str = ""  # cloud region; "" for on-prem or "your default region"
    instance_type: str = ""  # explicit GPU instance; "" => the provisioner sizes it
    gpu_vram_gb: float = 0.0  # the admin's provider-agnostic VRAM target (sizing.GpuTier.vram_gb);
    # each provider maps it to a concrete GPU (RunPod's serverless pool, a VPC GPU instance). 0 => unset
    context_k: float = 8.0  # target context length (thousands of tokens)
    concurrency: int = 4  # expected concurrent streams
    serve_wiki: bool = True  # also bring up the always-on wiki/index host
    gpu_count: int = 1  # single-node tensor-parallel size (docs/specs/multi-gpu-tensor-parallel-
    # serving.md); 1 = today's single-GPU behavior. gpu_vram_gb stays the PER-GPU target - aggregate
    # capacity is gpu_count * gpu_vram_gb, computed by sizing.py, not here.


@dataclass
class ProvisionStep:
    """One concrete action in a provisioning plan (shown to the admin before anything runs)."""

    name: str
    detail: str = ""


@dataclass
class ProvisionPlan:
    """The ordered steps a provisioner would run, plus the shape of the result."""

    provider: str
    tier: str  # onprem | vpc | neocloud
    serving_stack: str  # ollama | vllm | serverless
    steps: list[ProvisionStep] = field(default_factory=list)
    cold_start: bool = False  # true for serverless neocloud (first request after idle)
    notes: str = ""


@dataclass
class ProvisionResult:
    """Outcome of a real provisioning run."""

    ok: bool
    status: str  # provisioned | not_implemented | error
    detail: str = ""
    endpoint: OrgEndpoint | None = None  # the validated org endpoint on success
    handle: str = ""  # opaque id (instance / deploy) for teardown


@runtime_checkable
class Provisioner(Protocol):
    """Stand up + tear down one org serving backend. Implement these to add a provider."""

    key: str
    name: str
    tier: str
    serving_stack: str

    def available(self) -> tuple[bool, str]:
        """Is real provisioning wired up (creds/SDK present)? (ok, human_detail). No side effects."""
        ...

    def plan(self, spec: ProvisionSpec) -> ProvisionPlan:
        """Describe what provisioning would do. Pure - safe to call to render the UI."""
        ...

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        """Actually stand up the backend, deploy the model, validate, and return the endpoint.
        Planner stubs raise ``ProvisionerNotReady``."""
        ...

    def teardown(self, handle: str) -> tuple[bool, str]:
        """Release the provisioned resources (lifecycle). Stubs raise ``ProvisionerNotReady``."""
        ...


def endpoint_is_secure(endpoint: OrgEndpoint) -> bool:
    """Whether a provisioned endpoint is safe to serve without an explicit insecure opt-in.

    A live provisioner's endpoint is typically plaintext HTTP on a public IP (a bare vLLM server on a
    cloud VM) - transport is not authenticated or encrypted by default, so the API key and every
    prompt/response would travel unencrypted. Real TLS here is a deployment-architecture change (cert
    trust + retrieval; HTTPS with certificate checking turned off would be WORSE - it invites MITM
    while looking secure), tracked separately in docs/specs/llm-endpoint-transport-posture.md. Until
    that lands, secure-by-default means: refuse a cleartext endpoint unless the operator has explicitly
    opted in.

    Shared by every live provisioner (Lambda, DataCrunch, ...) so the posture never drifts between them.
    ``ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT`` is a hard override - it wins even if the insecure opt-in is
    set, so an operator who set it keeps exactly that behaviour regardless of what else is configured.

    A loopback endpoint (``127.0.0.1`` / ``localhost``) is also secure: it is how a bare-VM provisioner's
    SSH-tunnel channel (docs/specs/llm-endpoint-secure-transport.md, Option A) presents its endpoint - the
    real network hop is inside the tunnel, never cleartext, so this is checked (and accepted) before the
    insecure-opt-in logic below, and ``ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT`` does not refuse it either.
    """
    base = endpoint.base_url.strip().lower()
    if base.startswith("https://"):
        return True
    if urlsplit(endpoint.base_url).hostname in _LOOPBACK_HOSTS:
        return True
    allow_insecure = bool(os.environ.get("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT"))
    require_secure = bool(os.environ.get("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT"))
    return allow_insecure and not require_secure


# --- shared plan-step builders (pure) -------------------------------------------------------------


def _size_phrase(spec: ProvisionSpec) -> str:
    return f"{spec.params_b:g}B" if spec.params_b else "the chosen model"


def _gpu_phrase(spec: ProvisionSpec) -> str:
    """Human description of the GPU the admin's VRAM target implies, so the plan reflects the choice.

    An explicit ``instance_type`` (e.g. a Lambda instance the admin typed) wins; otherwise the
    provider-agnostic VRAM target names a GPU class. "" when neither is set (the provisioner sizes it).
    ``gpu_count`` > 1 (single-node tensor-parallel) is appended so the plan preview reflects it.
    """
    if spec.instance_type:
        phrase = spec.instance_type
    elif spec.gpu_vram_gb:
        phrase = f"a ~{spec.gpu_vram_gb:g} GB-class GPU"
    else:
        return ""
    if spec.gpu_count > 1:
        return f"{phrase}, tensor-parallel across {spec.gpu_count} GPUs"
    return phrase


def _wiki_step(spec: ProvisionSpec) -> list[ProvisionStep]:
    if not spec.serve_wiki:
        return []
    return [
        ProvisionStep(
            "wiki_host",
            "Bring up the always-on wiki / backend host - a small always-on CPU pod running the Anthill "
            "backend in your cloud (so it does not depend on this device).",
        )
    ]


def _validate_step() -> ProvisionStep:
    return ProvisionStep(
        "validate", "Run the end-to-end validation: reachable -> model served -> a real round-trip."
    )


def _ollama_steps(spec: ProvisionSpec) -> list[ProvisionStep]:
    return [
        ProvisionStep("check_host", "Confirm the on-prem host is reachable and has enough memory."),
        ProvisionStep("install_runtime", "Install or confirm Ollama on the host."),
        ProvisionStep(
            "pull_model",
            f"ollama pull {spec.model} - downloads the open weights you then own and run yourself.",
        ),
        ProvisionStep("serve", "Serve the model over its standard chat API on the LAN."),
        *_wiki_step(spec),
        _validate_step(),
    ]


def _vllm_steps(spec: ProvisionSpec, provider_name: str) -> list[ProvisionStep]:
    region = spec.region or "your default region"
    inst = _gpu_phrase(spec) or f"a GPU instance sized for {_size_phrase(spec)}"
    return [
        ProvisionStep(
            "authenticate", f"Authenticate to {provider_name} with your account credentials."
        ),
        ProvisionStep("launch_instance", f"Launch {inst} in {region}, inside your VPC."),
        ProvisionStep("install_runtime", "Install the vLLM serving stack on the instance."),
        ProvisionStep(
            "load_model", f"Load {spec.model} and start high-throughput batched serving."
        ),
        ProvisionStep(
            "expose", "Expose the endpoint privately inside your VPC, protected with a key."
        ),
        *_wiki_step(spec),
        _validate_step(),
    ]


def _serverless_steps(spec: ProvisionSpec, provider_name: str) -> list[ProvisionStep]:
    gpu = _gpu_phrase(spec)
    on_gpu = f" on {gpu}" if gpu else ""
    return [
        ProvisionStep("authenticate", f"Authenticate to {provider_name} with your account token."),
        ProvisionStep(
            "create_deploy",
            f"Create a serverless GPU deployment{on_gpu} serving {spec.model}.",
        ),
        ProvisionStep(
            "scale_policy",
            "Configure scale-to-zero with an optional warm pool to bound cold starts.",
        ),
        ProvisionStep("expose", "Expose the deployment's endpoint, protected with a key."),
        *_wiki_step(spec),
        _validate_step(),
    ]


# --- provisioners ---------------------------------------------------------------------------------


class _BaseProvisioner:
    """Shared planner behaviour. Real impls override ``provision``/``teardown``/``available``."""

    key = ""
    name = ""
    tier = ""
    serving_stack = ""
    cold_start = False
    eu_sovereign = False  # data stays in the EU (OVHcloud / Scaleway) - shown in the picker
    _needs = ""  # what wiring real provisioning requires (creds / SDK / installer)

    def available(self) -> tuple[bool, str]:
        return (
            False,
            f"{self.name} provisioning is not wired up yet. {self._needs} Until then, use the "
            "manual 'connect an endpoint I run myself' path.",
        )

    def _steps(self, spec: ProvisionSpec) -> list[ProvisionStep]:
        raise NotImplementedError

    def _notes(self, spec: ProvisionSpec) -> str:
        return ""

    def plan(self, spec: ProvisionSpec) -> ProvisionPlan:
        if spec.provider != self.key:
            raise ProvisionError(
                f"spec.provider '{spec.provider}' does not match provisioner '{self.key}'"
            )
        return ProvisionPlan(
            provider=self.key,
            tier=self.tier,
            serving_stack=self.serving_stack,
            steps=self._steps(spec),
            cold_start=self.cold_start,
            notes=self._notes(spec),
        )

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        raise ProvisionerNotReady(
            f"{self.name} provisioning is not implemented yet (Direction A impls ship "
            f"incrementally). {self._needs}"
        )

    def teardown(self, handle: str) -> tuple[bool, str]:
        raise ProvisionerNotReady(f"{self.name} teardown is not implemented yet.")


class OnPremProvisioner(_BaseProvisioner):
    key = "onprem"
    name = "On-prem"
    tier = "onprem"
    serving_stack = "ollama"
    _needs = "Provide host access (local or SSH) and run the on-prem installer."

    def _steps(self, spec: ProvisionSpec) -> list[ProvisionStep]:
        return _ollama_steps(spec)

    def _notes(self, spec: ProvisionSpec) -> str:
        return (
            "Ollama suits a Mac mini or CPU box; on a GPU rack, vLLM serves the same chat API at higher "
            "throughput. Fully inside your perimeter, no cloud egress."
        )


class _VpcProvisioner(_BaseProvisioner):
    tier = "vpc"
    serving_stack = "vllm"

    def _steps(self, spec: ProvisionSpec) -> list[ProvisionStep]:
        return _vllm_steps(spec, self.name)

    def _notes(self, spec: ProvisionSpec) -> str:
        return "Always-on in your own cloud account; green sovereignty (your VPC, your weights)."


class LambdaProvisioner(_VpcProvisioner):
    key = "lambda"
    name = "Lambda Labs"
    _needs = "Provide a Lambda Cloud API key for your own account."

    def _notes(self, spec: ProvisionSpec) -> str:
        return (
            "Primary Cloud VPC option: simple one-click GPU instances, competitively priced. Always-on "
            "(flat hourly), so it hosts the model + training + the wiki on one instance."
        )


class AwsProvisioner(_VpcProvisioner):
    key = "aws"
    name = "AWS"
    _needs = "Provide AWS credentials with EC2 + VPC permissions in your account."


class GcpProvisioner(_VpcProvisioner):
    key = "gcp"
    name = "GCP"
    _needs = "Provide a GCP service account with Compute Engine permissions in your project."


class AzureProvisioner(_VpcProvisioner):
    key = "azure"
    name = "Azure"
    _needs = (
        "Provide an Azure service principal with VM + network permissions in your subscription."
    )


class IbmProvisioner(_VpcProvisioner):
    key = "ibm"
    name = "IBM Cloud"
    _needs = "Provide an IBM Cloud API key with VPC infrastructure permissions in your account."


class OvhProvisioner(_VpcProvisioner):
    key = "ovh"
    name = "OVHcloud"
    eu_sovereign = True
    _needs = "Provide OVHcloud API credentials with Public Cloud / instance permissions."

    def _notes(self, spec: ProvisionSpec) -> str:
        return "EU-sovereign: data stays in the EU. Always-on in your own OVHcloud account."


class ScalewayProvisioner(_VpcProvisioner):
    key = "scaleway"
    name = "Scaleway"
    eu_sovereign = True
    _needs = "Provide a Scaleway API key with Instances permissions in your project."

    def _notes(self, spec: ProvisionSpec) -> str:
        return "EU-sovereign: data stays in the EU. Always-on in your own Scaleway account."


class DataCrunchPlanner(_VpcProvisioner):
    """The planner half; the live ``provision``/``teardown`` live in
    ``datacrunch_provision.DataCrunchLiveProvisioner``, which subclasses this - matching how
    ``LambdaProvisioner``/``LambdaLiveProvisioner`` split the planner from the live implementation."""

    key = "datacrunch"
    name = "DataCrunch / Verda"
    eu_sovereign = True
    _needs = "Provide a DataCrunch (Verda) client ID and client secret for your own account."

    def _notes(self, spec: ProvisionSpec) -> str:
        return (
            "EU-sovereign: data stays in Finland. Always-on in your own DataCrunch/Verda account."
        )


class NebiusProvisioner(_VpcProvisioner):
    """Planner stub. Live provisioning is intentionally NOT wired up: Nebius's compute API is only
    confirmed at the SDK-initialisation level (``nebius/pysdk``, IAM bearer token); the exact compute
    service-client class, request/response fields, and any startup-script mechanism could not be
    confirmed from public documentation, and inventing them would risk wrong requests once real
    credentials are involved. See docs/specs/model-onboarding-and-sovereignty.md for the provider list;
    this stays a planner (matching AWS/GCP/Azure/IBM's existing shape) until a human confirms the real
    compute API surface against an installed ``nebius-pysdk`` version."""

    key = "nebius"
    name = "Nebius"
    eu_sovereign = True
    _needs = (
        "Nebius needs its compute API surface confirmed against the installed nebius-pysdk package "
        "(the service-client class, create/get/delete request and response fields, and any "
        "startup-script mechanism) before live provisioning can be implemented."
    )


class _NeocloudProvisioner(_BaseProvisioner):
    tier = "neocloud"
    serving_stack = "serverless"
    cold_start = True

    def _steps(self, spec: ProvisionSpec) -> list[ProvisionStep]:
        return _serverless_steps(spec, self.name)

    def _notes(self, spec: ProvisionSpec) -> str:
        return _tier("neocloud").caveat


class RunpodProvisioner(_NeocloudProvisioner):
    key = "runpod"
    name = "RunPod"
    _needs = "Provide a RunPod API key for your own account."


# --- registry -------------------------------------------------------------------------------------


def _registry() -> dict[str, type[_BaseProvisioner]]:
    # RunPod (neocloud), Lambda (primary VPC), and DataCrunch (EU VPC) have live provisioners; the rest
    # are planner stubs until their own clients land behind the same interface (Nebius needs its real
    # compute API surface confirmed first - see NebiusProvisioner). Lazy import avoids a module cycle.
    from .datacrunch_provision import DataCrunchLiveProvisioner
    from .lambda_provision import LambdaLiveProvisioner
    from .runpod_provision import RunpodLiveProvisioner

    return {
        "onprem": OnPremProvisioner,
        "lambda": LambdaLiveProvisioner,
        "aws": AwsProvisioner,
        "gcp": GcpProvisioner,
        "azure": AzureProvisioner,
        "ibm": IbmProvisioner,
        "ovh": OvhProvisioner,
        "scaleway": ScalewayProvisioner,
        "datacrunch": DataCrunchLiveProvisioner,
        "nebius": NebiusProvisioner,
        "runpod": RunpodLiveProvisioner,
    }


def get_provisioner(provider: str) -> Provisioner:
    """The `Provisioner` for a provider key. Raises ``ProvisionError`` on an unknown key
    (so a typo fails loudly instead of silently provisioning the wrong place)."""
    key = (provider or "").strip().lower()
    reg = _registry()
    if key not in reg:
        raise ProvisionError(f"Unknown provider '{key}'. Known: {', '.join(PROVIDER_KEYS)}.")
    return reg[key]()


def providers_for_tier(tier_key: str) -> tuple[str, ...]:
    """The provider keys that belong to a hosting tier (onprem | vpc | neocloud), in picker order."""
    return tuple(p for p in PROVIDER_KEYS if _TIER_OF[p] == tier_key)


def tier_of(provider: str) -> str:
    """The hosting tier a provider runs in. Raises ``ProvisionError`` on an unknown key."""
    key = (provider or "").strip().lower()
    if key not in _TIER_OF:
        raise ProvisionError(f"Unknown provider '{key}'. Known: {', '.join(PROVIDER_KEYS)}.")
    return _TIER_OF[key]


def all_provisioners() -> list[Provisioner]:
    """Every provisioner, in picker order."""
    reg = _registry()
    return [reg[k]() for k in PROVIDER_KEYS]


def plan_summary(
    provider: str,
    model: str,
    *,
    params_b: float = 0.0,
    region: str = "",
    instance_type: str = "",
    gpu_vram_gb: float = 0.0,
    context_k: float = 8.0,
    concurrency: int = 4,
    serve_wiki: bool = True,
    gpu_count: int = 1,
) -> dict:
    """A JSON / template-friendly provisioning plan for one provider + model.

    Bundles the ordered steps Anthill would run with the readiness state, so a settings screen can
    show the admin exactly what will happen *and* that live provisioning is not wired up yet (with
    the manual-connect escape hatch). Raises ``ProvisionError`` on an unknown provider.
    """
    prov = get_provisioner(provider)
    spec = ProvisionSpec(
        provider=prov.key,
        model=model,
        params_b=params_b,
        region=region,
        instance_type=instance_type,
        gpu_vram_gb=gpu_vram_gb,
        context_k=context_k,
        concurrency=concurrency,
        serve_wiki=serve_wiki,
        gpu_count=gpu_count,
    )
    plan = prov.plan(spec)
    available, readiness = prov.available()
    return {
        "provider": prov.key,
        "provider_name": prov.name,
        "tier": plan.tier,
        "tier_name": _tier(plan.tier).name,
        "serving_stack": plan.serving_stack,
        "cold_start": plan.cold_start,
        "notes": plan.notes,
        "available": available,
        "readiness": readiness,
        "steps": [{"name": s.name, "detail": s.detail} for s in plan.steps],
        "escape_hatch": (
            "Until live provisioning is wired up, you can connect an endpoint you run yourself "
            "(the manual escape hatch)."
        ),
    }
