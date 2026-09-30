"""Org hosting: where the organization runs its shared wiki + model.

An org runs on infrastructure the *organization* owns - on-prem hardware, its own cloud VPC, or its own
neocloud account (Onehill hosts nothing). This package is the model-free foundation of that:

  tiers     - the three hosting categories + their sovereignty / billing metadata
  sizing    - given a box's memory, recommend the largest open model that runs *well*
  source    - discover available open models (built-in seed + Hugging Face + Ollama) with params + license
  endpoint  - validate an org model endpoint end to end (reachable -> model served -> round-trip)
  provision - provider-agnostic provisioning (Direction A): plan + stand up an org serving backend

The settings UI + OrgSettings fields wire these together in later P1 work. See
engineering-plans/PERSONAL_AND_ORG_PLANES.md (internal).
"""

from __future__ import annotations

from .endpoint import Check, OrgEndpoint, Validation, validate
from .provision import (
    PROVIDER_KEYS,
    ProvisionerNotReady,
    ProvisionError,
    ProvisionPlan,
    ProvisionResult,
    ProvisionSpec,
    ProvisionStep,
    all_provisioners,
    get_provisioner,
    plan_summary,
    providers_for_tier,
    tier_of,
)
from .sizing import (
    DEFAULT_CATALOG,
    DEFAULT_LOCAL_FAMILY,
    LOCAL_CATALOG,
    LOCAL_FAMILIES,
    FamilyPick,
    Model,
    ModelFit,
    Sizing,
    family_download_gb,
    local_hardware,
    max_params_b,
    recommend,
    recommend_by_family,
    usable_gb,
)
from .source import (
    CatalogModel,
    builtin_catalog,
    params_from_name,
)
from .tiers import TIER_KEYS, TIERS, HostingTier, tier

__all__ = [
    "DEFAULT_CATALOG",
    "DEFAULT_LOCAL_FAMILY",
    "LOCAL_CATALOG",
    "LOCAL_FAMILIES",
    "PROVIDER_KEYS",
    "TIERS",
    "TIER_KEYS",
    "CatalogModel",
    "Check",
    "FamilyPick",
    "HostingTier",
    "Model",
    "ModelFit",
    "OrgEndpoint",
    "ProvisionError",
    "ProvisionPlan",
    "ProvisionResult",
    "ProvisionSpec",
    "ProvisionStep",
    "ProvisionerNotReady",
    "Sizing",
    "Validation",
    "all_provisioners",
    "builtin_catalog",
    "family_download_gb",
    "get_provisioner",
    "local_hardware",
    "max_params_b",
    "params_from_name",
    "plan_summary",
    "providers_for_tier",
    "recommend",
    "recommend_by_family",
    "tier",
    "tier_of",
    "usable_gb",
    "validate",
]
