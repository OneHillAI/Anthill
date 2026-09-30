"""Reap orphaned RunPod training pods - the launch-window / orchestrator-death leak net (#254).

``RunpodTrainer.train()`` tears its pod down in a ``try/finally``, so a training pod only survives if:
  - the orchestrator process died between ``launch()`` and the ``finally`` (a crash, a killed worker,
    a redeploy mid-run), or
  - ``launch()`` partially created a pod and then raised - the one edge that sits *outside* the
    try/finally (flagged by the #254 pre-flight audit).

In every case a rented GPU keeps billing until something terminates it. This is that something: it lists
the account's ``anthill-train-*`` pods and terminates any that have outlived a run.

Design mirrors ``RunpodTrainer``: it is pure and injectable. The reaping logic takes a ``RunpodPodClient``
(the same thin surface the trainer uses, extended with ``list_pods``), so it is unit-tested with a fake
client - no network, no live account, no spend. Only ``reaper_client_for_cfg`` touches real config; the
scheduler runs ``sweep`` on a slow cadence and the CLI (``anthill train-reap``) runs it on demand.

Safety: the reaper must never kill a healthy in-flight training pod. The threshold is chosen by
``plan_max_age`` from whether a run is currently active, so an in-flight run's pod is always outside the
reap window; a pod is only reaped once it is provably older than any legitimate run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backends.runpod_train import TRAIN_POD_PREFIX, PodInfo, RunpodPodClient

# No run is active: any anthill-train pod is orphaned. The grace covers a pod that was just launched by a
# run whose ``TrainingRun`` row has not flipped to ``running`` yet (a brief window in ``execute_run``).
REAP_GRACE_MIN = 15.0
# A run IS active: a pod may be legitimately training, so only reap one that has outlived even the longest
# run the org could configure (its ``max_runtime`` + this margin), which no healthy run ever reaches.
REAP_MARGIN_MIN = 60.0


@dataclass
class ReapResult:
    """What a sweep saw and did. Never carries an exception - a per-pod failure lands in ``errors`` and
    the sweep keeps going (terminate is idempotent, so a retry next tick is harmless)."""

    scanned: int = 0  # pods on the account
    ours: int = 0  # anthill-train-* pods among them
    max_age_minutes: float = 0.0  # the threshold this sweep applied
    reaped: list = field(
        default_factory=list
    )  # [(id, name, age_min)] terminated (or, in dry-run, would)
    skipped_young: list = field(
        default_factory=list
    )  # [(id, name, age_min)] still within the window
    errors: list = field(default_factory=list)  # [(id, name, detail)]
    dry_run: bool = False

    def summary(self) -> str:
        verb = "would reap" if self.dry_run else "reaped"
        return (
            f"scanned {self.scanned} pod(s), {self.ours} anthill-train, {verb} {len(self.reaped)}, "
            f"{len(self.skipped_young)} within the {self.max_age_minutes:.0f}m window, "
            f"{len(self.errors)} error(s)"
        )


def reap_orphaned_pods(
    client: RunpodPodClient, *, max_age_minutes: float, dry_run: bool = False
) -> ReapResult:
    """Terminate every ``anthill-train-*`` pod at least ``max_age_minutes`` old. Never raises: a failed
    ``list_pods`` returns an empty-but-errored result, and a per-pod terminate error is recorded while
    the sweep continues."""
    res = ReapResult(max_age_minutes=float(max_age_minutes), dry_run=dry_run)
    try:
        pods = client.list_pods()
    except Exception as e:  # best-effort safety net: the reaper never raises into its caller
        res.errors.append(("", "", f"list_pods failed: {e}"))
        return res
    res.scanned = len(pods)
    for p in pods:
        if not _is_training_pod(p):
            continue
        res.ours += 1
        if p.age_minutes < max_age_minutes:
            res.skipped_young.append((p.id, p.name, p.age_minutes))
            continue
        if dry_run:
            res.reaped.append((p.id, p.name, p.age_minutes))
            continue
        try:
            client.terminate(p.id)
            res.reaped.append((p.id, p.name, p.age_minutes))
        except Exception as e:  # record and keep sweeping the rest
            res.errors.append((p.id, p.name, str(e)))
    return res


def plan_max_age(*, active_run: bool, run_max_min: float) -> float:
    """The safe reap threshold. With a run active a pod may be legitimately training, so only reap one
    that has outlived even the longest configured run (``run_max_min`` + ``REAP_MARGIN_MIN``); with no
    run active, any training pod is orphaned, so reap after a short grace."""
    if active_run:
        return float(run_max_min) + REAP_MARGIN_MIN
    return REAP_GRACE_MIN


def sweep(
    client: RunpodPodClient, *, active_run: bool, run_max_min: float, dry_run: bool = False
) -> ReapResult:
    """``reap_orphaned_pods`` with the threshold chosen by ``plan_max_age``. Pure and testable."""
    return reap_orphaned_pods(
        client,
        max_age_minutes=plan_max_age(active_run=active_run, run_max_min=run_max_min),
        dry_run=dry_run,
    )


def _is_training_pod(p: PodInfo) -> bool:
    return (p.name or "").startswith(TRAIN_POD_PREFIX)


# ── config glue (the only part that reads OrgSettings / builds the real client) ───────────────────────


def trains_on_runpod(cfg) -> bool:
    """True iff this org fine-tunes on a rented RunPod pod (backend ``endpoint`` + provider ``runpod``) -
    the only backend that can leak a pod. On-prem / cloud-VM / MLX tear down differently."""
    from .backends import normalize_key

    if normalize_key(getattr(cfg, "training_backend", None)) != "endpoint":
        return False
    provider = (getattr(cfg, "training_provider", "") or "runpod").strip().lower()
    return provider == "runpod"


def reaper_client_for_cfg(cfg) -> RunpodPodClient | None:
    """A real RunPod client for this org, or None when the org does not train on RunPod or has no key
    (so the caller simply skips it). The key is the org's cloud provisioning key, the same one the
    trainer uses."""
    if not trains_on_runpod(cfg):
        return None
    from .backends.endpoint import _runpod_key
    from .backends.runpod_train import _RealRunpodPodClient

    key = _runpod_key(cfg)
    if not key:
        return None
    try:
        return _RealRunpodPodClient(key)
    except Exception:
        return None


def configured_run_max_min(cfg) -> int:
    """The org's configured max training runtime (minutes), clamped to the 24h hard ceiling - the same
    bound the endpoint backend applies, so the reaper's 'active run' window matches a real run."""
    try:
        return min(int(getattr(cfg, "aws_max_runtime_min", 0) or 120), 1440)
    except (TypeError, ValueError):
        return 120
