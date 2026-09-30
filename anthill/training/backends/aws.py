"""AWS training backend: ephemeral GPU in the org's own AWS account.

Thin adapter over ``anthill.cloud.aws`` (which already does credential validation, tagged +
capped provisioning, per-org least-privilege IAM, and **guaranteed teardown**). This class
just exposes that lifecycle through the provider-agnostic `TrainingBackend` interface; the
GPU-side fine-tune itself is the shared ``trainer.py`` step (wired next).
"""

from __future__ import annotations

from ...cloud import aws as _aws
from .base import BackendError, TrainingResult


class AwsBackend:
    name = "aws"

    def validate(self, cfg) -> tuple[bool, str]:
        # Read-only STS check; no GPU launched.
        return _aws.validate(cfg)

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        """Provision a tagged, capped GPU, run the trainer, and ALWAYS tear it down.

        ``cloud.aws.provision_and_train`` owns the launch + try/finally teardown + reaper
        safety net; the remote LoRA step (``trainer.py``) is wired next, so until then this
        provisions, refuses cleanly, and tears the instance back down."""
        try:
            result = _aws.provision_and_train(cfg, dataset_path)
        except NotImplementedError as e:
            # The provisioning + guaranteed teardown ran; only the GPU-side trainer is pending.
            raise BackendError(f"AWS trainer step not wired yet (trainer.py): {e}") from e
        except Exception as e:
            raise BackendError(f"AWS training run failed: {e}") from e
        inst = result.get("instance", {})
        training = result.get("training", {}) or {}
        return TrainingResult(
            adapter_path=training.get("adapter_path", ""),
            won_eval=bool(training.get("won_eval", False)),
            cost_usd_est=float(inst.get("est_cost_usd", 0.0)),
            detail=training.get("detail", "AWS run complete"),
            model_version=training.get("model_version"),
        )
