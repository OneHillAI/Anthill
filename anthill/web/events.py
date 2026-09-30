"""Event-driven proactivity.

ACTIONS (drain inbox, react to new inputs) run event-driven - the scheduler's
fast tick reacts as soon as an input appears, rather than waiting for a batch
window. TRAINING is deliberately NOT here: it stays batched (the 24h gold-delta
trigger), because per-event fine-tuning wastes GPU/energy/cost.

This module holds the pure decision logic (testable without a model or network).
The first event source is the workspace inbox/; webhooks (Slack), IMAP IDLE
(email) and a filesystem watcher are the natural push upgrades on top.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def inbox_files(ws_path: str) -> list[Path]:
    """Files awaiting ingestion in the workspace inbox/."""
    inbox = Path(ws_path) / "inbox"
    if not inbox.is_dir():
        return []
    return [p for p in sorted(inbox.iterdir()) if p.is_file() and not p.name.startswith(".")]


def should_drain(
    mode: str,
    has_files: bool,
    last_run,  # datetime | None
    interval_s: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether to process pending inputs now.

    event     → act as soon as there's anything to do (real-time feel).
    scheduled → act only once the interval has elapsed (batched).
    """
    now = now or datetime.now(timezone.utc)
    if not has_files:
        return False, "nothing pending"
    if mode == "event":
        return True, "event: input pending"
    # scheduled
    if last_run is not None:
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if now - last_run < timedelta(seconds=max(1, interval_s)):
            return False, "scheduled: within interval window"
    return True, "scheduled: interval elapsed"
