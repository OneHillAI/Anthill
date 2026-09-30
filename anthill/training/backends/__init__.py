"""Training-backend registry: pick where a fine-tune runs from one setting.

`get_backend(cfg)` maps ``OrgSettings.training_backend`` to a `TrainingBackend`. Adding a
provider = implement the interface (`base.py`) and register it here; nothing else changes.

Canonical keys: ``onprem | aws | ibm | gcp | azure | endpoint``.
  - ``aws``/``ibm``/``gcp``/``azure`` - the org's own cloud account (ephemeral GPU VM); full
    perimeter sovereignty (IBM is listed right after AWS).
  - ``onprem`` - the org's own hardware (max control, zero cloud egress).
  - ``endpoint`` - the org's OWN rented neocloud account (RunPod / Modal): runs on third-party
    shared GPUs, so the data technically leaves the org's account - the Settings panel says so
    on selection, and the data-privacy invariants in ``base.py`` are what protect it. There is
    NO Onehill-mediated option; the org always supplies its own provider key.
``vpc`` is accepted as a back-compat alias for ``aws``.
"""

from __future__ import annotations

from .base import BackendError, TrainingBackend, TrainingResult

__all__ = [
    "BACKEND_KEYS",
    "BackendError",
    "TrainingBackend",
    "TrainingResult",
    "get_backend",
    "local_mac_training_available",
    "training_backend_for_provider",
]

# back-compat: the old onprem|vpc value set mapped vpc -> the org's cloud VPC GPU (AWS).
# ``local`` is a friendly alias for the Apple-Silicon MLX backend.
_ALIASES = {"vpc": "aws", "local": "mac"}

# Canonical keys, in the order shown in the Settings picker.
# ``mac`` = fine-tune on this Apple-Silicon Mac via MLX (no Docker/NVIDIA); on-prem auto-uses
# it when the box is a Mac with mlx-lm and no separate SSH GPU host is configured.
BACKEND_KEYS = ("onprem", "mac", "aws", "ibm", "gcp", "azure", "endpoint")

# Training is NOT configured separately from serving: it runs on the SAME account the org serves its
# model from. This maps the org's serving provider (OrgSettings.org_provider) to the training backend
# that reuses that account. Providers without a matching training backend yet (lambda/ovh/scaleway)
# map to ("", "") - training is simply not available there until a backend exists.
_ORG_PROVIDER_TO_TRAINING: dict[str, tuple[str, str]] = {
    "onprem": ("onprem", ""),
    "runpod": ("endpoint", "runpod"),
    "modal": ("endpoint", "modal"),
    "aws": ("aws", ""),
    "gcp": ("gcp", ""),
    "azure": ("azure", ""),
    "ibm": ("ibm", ""),
}


def training_backend_for_provider(org_provider: str) -> tuple[str, str]:
    """Derive ``(training_backend, training_provider)`` from the org's serving cloud, so training reuses
    the same account. ("", "") when that provider has no training backend yet (lambda/ovh/scaleway)."""
    return _ORG_PROVIDER_TO_TRAINING.get((org_provider or "").strip().lower(), ("", ""))


def local_mac_training_available() -> bool:
    """True when this host can fine-tune locally (Apple Silicon + mlx-lm). Lazy-imported so
    the check costs nothing on non-Mac hosts."""
    from .mac import local_mac_training_available as _impl

    return _impl()


def _registry() -> dict[str, type]:
    """Lazy: import provider classes on demand so optional SDKs (boto3, etc.) are never
    imported until a backend is actually selected."""
    from .aws import AwsBackend
    from .clouds import AzureBackend, GcpBackend, IbmBackend
    from .endpoint import EndpointBackend
    from .mac import MacBackend
    from .onprem import OnPremBackend

    return {
        "onprem": OnPremBackend,
        "mac": MacBackend,
        "aws": AwsBackend,
        "gcp": GcpBackend,
        "azure": AzureBackend,
        "ibm": IbmBackend,
        "endpoint": EndpointBackend,
    }


def normalize_key(value: str | None) -> str:
    """Normalize a raw ``training_backend`` value to a canonical key (applies aliases)."""
    key = (value or "onprem").strip().lower()
    return _ALIASES.get(key, key)


def get_backend(cfg) -> TrainingBackend:
    """Return the `TrainingBackend` selected by ``cfg.training_backend``.

    Raises ``BackendError`` for an unknown backend (so a typo fails loudly, never silently
    runs the wrong place)."""
    key = normalize_key(getattr(cfg, "training_backend", None))
    reg = _registry()
    if key not in reg:
        raise BackendError(f"Unknown training backend '{key}'. Known: {', '.join(sorted(reg))}.")
    return reg[key]()
