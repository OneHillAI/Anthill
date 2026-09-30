"""Org-cloud training backends: GCP / Azure / IBM (the org's own cloud account).

Same shape as the AWS backend - an ephemeral GPU VM (or managed training job) launched in
the org's *own* cloud tenancy, with the same invariants (guaranteed teardown, cost caps,
eval-gated, per-tenant). Full perimeter sovereignty: nothing leaves the org's account.

Stubs for now: each is added by implementing `run()` against its SDK (GCP Compute Engine /
Vertex AI, Azure VM / Azure ML, IBM Cloud VPC GPU), mirroring ``cloud/aws.py``. Credential
validation is filled in with each implementation; `validate()` reports config presence today.
"""

from __future__ import annotations

from .base import BackendError, TrainingResult


class _CloudVMBackend:
    """Shared base for org-owned cloud GPU backends (ephemeral VM, guaranteed teardown)."""

    name = "cloud"
    provider_label = "cloud"

    def validate(self, cfg) -> tuple[bool, str]:
        region = (
            getattr(cfg, f"{self.name}_region", "") or getattr(cfg, "aws_region", "") or ""
        ).strip()
        if not region:
            return False, f"{self.provider_label} backend needs a region configured in Settings."
        return (
            True,
            f"{self.provider_label} region {region} configured ({self.provider_label} backend pending).",
        )

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        raise BackendError(
            f"{self.provider_label} training backend not yet implemented: launch an ephemeral "
            f"GPU VM in the org's {self.provider_label} account, run trainer.py, fetch the "
            f"adapter, and guarantee teardown (mirror cloud/aws.py)."
        )


class GcpBackend(_CloudVMBackend):
    name = "gcp"
    provider_label = "GCP"


class AzureBackend(_CloudVMBackend):
    name = "azure"
    provider_label = "Azure"


class IbmBackend(_CloudVMBackend):
    name = "ibm"
    provider_label = "IBM Cloud"
