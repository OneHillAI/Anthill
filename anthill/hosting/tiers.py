"""The three org-hosting categories (the "Your cloud" choice).

The org backend runs on infrastructure the organization itself owns. Onehill hosts nothing, and the
org backend never lives on an individual user's personal device. These are NOT the paid per-token vendor
fallback (anthill/hybrid), which leaves the perimeter and is never an org-hosting option.

Sovereignty label:
  green  - fully inside your perimeter (your hardware or your own always-on cloud VPC)
  amber  - your own account and your own model, but inference transits a third party's shared GPUs
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostingTier:
    key: str
    name: str
    always_on: bool  # an always-on box vs a per-use (serverless) GPU
    sovereignty: str  # "green" | "amber"
    billing: str
    description: str
    caveat: str = ""  # the amber caveat for neocloud; empty for the green tiers


TIERS: dict[str, HostingTier] = {
    "onprem": HostingTier(
        key="onprem",
        name="On-prem",
        always_on=True,
        sovereignty="green",
        billing="one-time hardware",
        description="Your own machine, from a Mac mini in a closet up to a GPU rack. "
        "Always-on and fully inside your perimeter.",
    ),
    "vpc": HostingTier(
        key="vpc",
        name="Cloud VPC",
        always_on=True,
        sovereignty="green",
        billing="always-on, flat",
        description="Your organization's own cloud VPC (AWS / GCP / Azure / IBM). "
        "Always-on, your account.",
    ),
    "neocloud": HostingTier(
        key="neocloud",
        name="Neocloud",
        always_on=False,
        sovereignty="amber",
        billing="per-use",
        description="Your org's own RunPod account, billed per use. "
        "Cheapest for bursty or small teams.",
        caveat="Runs on a third party's shared GPUs, so data transits their hardware during a run; "
        "and per-use billing means a cold start on the first request after idle.",
    ),
}

TIER_KEYS = ("onprem", "vpc", "neocloud")


def tier(key: str) -> HostingTier:
    """The HostingTier for a key (raises KeyError on an unknown key)."""
    return TIERS[key]
