"""Decide whether a local training run should fire (§7.5 'when').

Pure logic, no DB or model dependencies, so it is trivially testable:
every 24h check whether new gold examples accumulated since the last run;
train only then, and only if the cadence window has elapsed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class _Settings(Protocol):
    training_enabled: bool
    training_backend: str  # "onprem" | "vpc"
    training_gpu_endpoint: str
    training_schedule_hrs: int
    training_gold_mark: int
    training_last_run: object  # datetime | None


def should_train(
    settings: _Settings,
    gold_count: int,
    now: datetime | None = None,
    enabled: bool | None = None,
) -> tuple[bool, str]:
    """Return (should_run, human-readable reason).

    Fires only when ALL hold:
      - training is enabled (``enabled`` overrides the stored flag - local/solo is always-on)
      - a usable backend is configured (VPC needs an endpoint; on-prem may be local)
      - new gold examples exist since the last run's watermark
      - the cadence window (default 24h) has elapsed since the last run
    """
    now = now or datetime.now(timezone.utc)

    if enabled is None:
        enabled = getattr(settings, "training_enabled", False)
    if not enabled:
        return False, "training disabled"

    backend = getattr(settings, "training_backend", "onprem")
    endpoint = getattr(settings, "training_gpu_endpoint", "") or ""
    if backend == "vpc" and not endpoint:
        return False, "VPC backend selected but no GPU endpoint configured"

    new_gold = gold_count - int(getattr(settings, "training_gold_mark", 0) or 0)
    if new_gold <= 0:
        return False, "no new gold examples since last run"

    last_run = getattr(settings, "training_last_run", None)
    if last_run is not None:
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        window = timedelta(hours=int(getattr(settings, "training_schedule_hrs", 24) or 24))
        if now - last_run < window:
            hrs = round((window - (now - last_run)).total_seconds() / 3600, 1)
            return False, f"within cadence window ({hrs}h until next eligible run)"

    return True, f"{new_gold} new gold example(s) since last run"
