"""Provider-agnostic training-backend interface (ARCHITECTURE §7.5).

A *training backend* turns gold examples + a base model into a promoted org model on
*some* GPU, and is interchangeable: the org (or Onehill) picks where the GPU lives -
their own **AWS / GCP / Azure / IBM** account, an **on-prem** box, or a rented
**neocloud endpoint** (RunPod / Modal / any HTTP+container) - by setting one value. We
run on the exact same interface our users do; there is no privileged path.

Every backend MUST uphold these invariants (non-negotiable, CLAUDE.md / §7.5):
  - **Guaranteed teardown:** the GPU is released on success *and* failure (try/finally;
    cloud reaper / endpoint idle-stop). A crashed run never leaves a billable instance.
  - **Cost guardrails:** instance-type + max-runtime caps; refuse past the per-run budget;
    surface the estimated worst-case cost before launching.
  - **Data privacy:** only **gold**, **PII-scrubbed before it leaves a node**, encrypted in
    transit. This matters most for the neocloud endpoint - the provider rents the GPU but
    the *data* is still the org's private data, so it must never see anything but scrubbed
    gold, never persist it, and never co-mingle one org's data with another's.
  - **Eval-gated promotion:** a candidate replaces the current model only if it wins on a
    held-out gold slice (`lifecycle.evaluate`).
  - **Per-tenant isolation:** per-org tagging/scoping end to end.

`run()` receives a `dataset_path` that is ALREADY PII-scrubbed gold (the caller scrubs via
`hybrid.scrub` before handing it over). Backends transport and train on it; they do not
re-derive it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BackendError(RuntimeError):
    """A training backend refused or failed (bad config, guardrail, or provider error).

    Raising this (rather than a bare provider exception) keeps the executor's handling
    uniform across providers. Backends must still have torn the GPU down before raising.
    """


@dataclass
class TrainingResult:
    """Outcome of one training run on a backend."""

    adapter_path: str  # local path to the produced LoRA adapter ("" if none kept)
    won_eval: bool  # beat the current model on held-out gold?
    cost_usd_est: float  # estimated worst-case spend for the run
    detail: str  # human-readable summary (for the TrainingRun audit row)
    model_version: int | None = None  # vN if promoted, else None


@runtime_checkable
class TrainingBackend(Protocol):
    """Where a fine-tune actually runs. Implement these two methods to add a provider."""

    name: str

    def validate(self, cfg) -> tuple[bool, str]:
        """Read-only creds/connectivity check (no GPU launched). (ok, human_detail)."""
        ...

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        """Provision a GPU, run the shared trainer on the (pre-scrubbed) dataset, fetch the
        adapter, eval-gate, and ALWAYS tear the GPU down (try/finally). `run` is the optional
        ``TrainingRun`` audit row to update. Raises ``BackendError`` on refusal/failure."""
        ...
