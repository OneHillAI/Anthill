"""On-prem training backend: the org's own hardware (max control, zero cloud egress).

"On-prem" means the org's own box, whatever it is:
  - With a ``training_gpu_endpoint`` (an SSH host the org owns): SSH in, run the pinned trainer
    container there (``docker run --gpus all``), and fetch the LoRA adapter back. That box needs
    SSH key auth, Docker, and an NVIDIA runtime.
  - With NO endpoint, on an Apple-Silicon Mac with mlx-lm: train right here via MLX (no Docker /
    NVIDIA) - the Mac Mini / Studio serves AND trains. Delegated to ``MacBackend``.

Either way nothing is torn down (the org owns the hardware) and only PII-scrubbed gold is used.
See engineering-plans/MAC_MINI_ON_PREM.md.
"""

from __future__ import annotations

from .base import BackendError, TrainingResult
from .mac import MacBackend, local_mac_training_available

_NEEDS_HOST = (
    "On-prem backend needs a GPU endpoint (SSH host) in Settings, or an Apple-Silicon Mac with "
    "mlx-lm for local training."
)


class OnPremBackend:
    name = "onprem"

    def validate(self, cfg) -> tuple[bool, str]:
        endpoint = (getattr(cfg, "training_gpu_endpoint", "") or "").strip()
        if not endpoint:
            if local_mac_training_available():
                return MacBackend().validate(cfg)  # train on this Mac via MLX
            return False, _NEEDS_HOST
        return (
            True,
            f"On-prem GPU endpoint configured: {endpoint}. Run Train now to confirm end to end "
            "(the box needs SSH key auth + Docker + an NVIDIA runtime).",
        )

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        from ..remote import SSHHost, containerized_train, parse_endpoint

        endpoint = (getattr(cfg, "training_gpu_endpoint", "") or "").strip()
        if not endpoint:
            if local_mac_training_available():
                return MacBackend().run(
                    cfg, dataset_path=dataset_path, base_model=base_model, run=run
                )
            raise BackendError(_NEEDS_HOST)
        host: SSHHost = parse_endpoint(endpoint)
        adapter = containerized_train(host, dataset_path=dataset_path, base_model=base_model)
        return TrainingResult(
            adapter_path=adapter,
            won_eval=False,  # the executor's eval-gate decides promotion
            cost_usd_est=0.0,  # your own hardware
            detail=f"trained on on-prem GPU box {host.host} (container)",
        )
