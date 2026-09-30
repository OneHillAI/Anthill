"""Apple-Silicon local training backend: fine-tune on THIS Mac's GPU via MLX.

No Docker, no NVIDIA, no cloud - the LoRA fine-tune runs in-process through the shared
trainer's MLX path (``anthill.training.trainer``), so a Mac Mini / Studio can both **serve
and train** the org's adapter on one box. The executor still scrubs gold before calling
``run()`` and eval-gates the produced adapter after (org-side), exactly as for the remote
backends; this backend only does the GPU step. Nothing to tear down (your own hardware) and
no data leaves the machine.

See engineering-plans/MAC_MINI_ON_PREM.md (section 3).
"""

from __future__ import annotations

import os

from ...hosting.appliance import is_apple_silicon
from ..trainer import TrainerError, detect_toolchain, train_adapter
from .base import BackendError, TrainingResult


def local_mac_training_available() -> bool:
    """True when this host can fine-tune locally: Apple Silicon with mlx-lm installed."""
    return is_apple_silicon() and detect_toolchain() == "mlx"


class MacBackend:
    """Fine-tune locally on Apple Silicon via MLX (no Docker/NVIDIA, no egress)."""

    name = "mac"

    def validate(self, cfg) -> tuple[bool, str]:
        if not is_apple_silicon():
            return False, "Local training needs an Apple-Silicon Mac (this host is not one)."
        if detect_toolchain() != "mlx":
            return (
                False,
                "Install mlx-lm to train on this Mac: pip install mlx-lm (no Docker/NVIDIA needed).",
            )
        return (
            True,
            "Apple-Silicon MLX local training ready - fine-tunes on this Mac's GPU, "
            "no Docker/NVIDIA, no cloud egress.",
        )

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        out_dir = os.path.join(os.path.dirname(dataset_path) or ".", "mlx-adapter")
        try:
            adapter = train_adapter(dataset_path, base_model, out_dir=out_dir, toolchain="mlx")
        except TrainerError as e:
            raise BackendError(f"Local MLX training failed: {e}") from e
        return TrainingResult(
            adapter_path=adapter,
            won_eval=False,  # the executor eval-gates + promotes org-side
            cost_usd_est=0.0,  # your own Mac
            detail="trained locally on Apple Silicon (MLX) - no GPU rental, no data egress",
        )
