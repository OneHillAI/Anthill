"""Tests for the 24h gold-delta training trigger (§7.5 'when')."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from anthill.training.schedule import should_train


def _cfg(**kw):
    base = {
        "training_enabled": True,
        "training_backend": "onprem",
        "training_gpu_endpoint": "",
        "training_schedule_hrs": 24,
        "training_gold_mark": 10,
        "training_last_run": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


NOW = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def test_fires_when_new_gold_and_never_run():
    go, reason = should_train(_cfg(training_gold_mark=10), gold_count=15, now=NOW)
    assert go and "5 new gold" in reason


def test_skips_when_no_new_gold():
    go, reason = should_train(_cfg(training_gold_mark=15), gold_count=15, now=NOW)
    assert not go and "no new gold" in reason


def test_skips_when_disabled():
    go, reason = should_train(_cfg(training_enabled=False), gold_count=99, now=NOW)
    assert not go and "disabled" in reason


def test_vpc_requires_endpoint():
    go, reason = should_train(
        _cfg(training_backend="vpc", training_gpu_endpoint=""), gold_count=99, now=NOW
    )
    assert not go and "endpoint" in reason


def test_vpc_with_endpoint_fires():
    go, _ = should_train(
        _cfg(training_backend="vpc", training_gpu_endpoint="https://gpu.vpc"),
        gold_count=99,
        now=NOW,
    )
    assert go


def test_respects_24h_cadence():
    # Last run 2h ago, new gold exists → must wait
    go, reason = should_train(
        _cfg(training_last_run=NOW - timedelta(hours=2)), gold_count=99, now=NOW
    )
    assert not go and "cadence window" in reason


def test_fires_after_cadence_elapsed():
    go, _ = should_train(_cfg(training_last_run=NOW - timedelta(hours=25)), gold_count=99, now=NOW)
    assert go


def test_naive_last_run_treated_as_utc():
    # A naive datetime (no tzinfo) must not crash the comparison
    go, _ = should_train(
        _cfg(training_last_run=datetime(2026, 6, 1, 12, 0)), gold_count=99, now=NOW
    )
    assert go  # 3 days ago, well past cadence
