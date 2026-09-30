"""The RunPod training-pod reaper (#254): terminate a pod leaked when the orchestrator died between
launch and the trainer's try/finally teardown. Pure and injectable - a fake client, no network, no spend.
"""

from __future__ import annotations

import types

import pytest

from anthill.training import reaper
from anthill.training.backends.runpod_train import TRAIN_POD_PREFIX, PodInfo, RunpodTrainer


class FakeClient:
    """A RunpodPodClient the reaper drives: list some pods, record terminations."""

    def __init__(self, pods, *, fail_list=False, fail_terminate=()):
        self._pods = pods
        self._fail_list = fail_list
        self._fail_terminate = set(fail_terminate)
        self.terminated: list[str] = []

    def list_pods(self):
        if self._fail_list:
            raise RuntimeError("provider down")
        return list(self._pods)

    def terminate(self, pod_id):
        if pod_id in self._fail_terminate:
            raise RuntimeError("terminate failed")
        self.terminated.append(pod_id)


def _pod(pid, name, age):
    return PodInfo(id=pid, name=name, age_minutes=age)


def test_reaps_only_old_training_pods():
    pods = [
        _pod("a", "anthill-train-qwen3-8b", 200),  # old training pod -> reap
        _pod("b", "anthill-train-qwen3-8b", 5),  # young training pod -> keep
        _pod("c", "some-other-workload", 999),  # not ours -> ignore
        _pod("d", "anthill-train-mistral-7b", 30),  # old training pod -> reap
    ]
    client = FakeClient(pods)
    res = reaper.reap_orphaned_pods(client, max_age_minutes=15)
    assert client.terminated == ["a", "d"]
    assert res.scanned == 4 and res.ours == 3
    assert {r[0] for r in res.reaped} == {"a", "d"}
    assert [s[0] for s in res.skipped_young] == ["b"]
    assert not res.errors


def test_dry_run_reports_but_terminates_nothing():
    client = FakeClient([_pod("a", "anthill-train-x", 200)])
    res = reaper.reap_orphaned_pods(client, max_age_minutes=15, dry_run=True)
    assert res.dry_run and [r[0] for r in res.reaped] == ["a"]
    assert client.terminated == []  # nothing actually killed
    assert "would reap" in res.summary()


def test_plan_max_age_active_vs_idle():
    # A run is active: only reap a pod that outlived even the longest configured run.
    assert reaper.plan_max_age(active_run=True, run_max_min=120) == 120 + reaper.REAP_MARGIN_MIN
    # No run active: any training pod is orphaned, reap after the short grace.
    assert reaper.plan_max_age(active_run=False, run_max_min=120) == reaper.REAP_GRACE_MIN


def test_sweep_never_reaps_an_inflight_run_pod():
    # With a run active and max_runtime 120, the window is 180m. A 150m pod could be that live run:
    # it must NOT be reaped. A 200m pod has outlived any legit run and IS reaped.
    client = FakeClient(
        [_pod("live", "anthill-train-x", 150), _pod("leak", "anthill-train-x", 200)]
    )
    res = reaper.sweep(client, active_run=True, run_max_min=120)
    assert client.terminated == ["leak"]
    assert [s[0] for s in res.skipped_young] == ["live"]


def test_sweep_idle_reaps_after_grace():
    client = FakeClient([_pod("old", "anthill-train-x", 20), _pod("new", "anthill-train-x", 5)])
    res = reaper.sweep(client, active_run=False, run_max_min=120)
    assert client.terminated == ["old"]
    assert [s[0] for s in res.skipped_young] == ["new"]


def test_list_pods_failure_is_swallowed():
    res = reaper.reap_orphaned_pods(FakeClient([], fail_list=True), max_age_minutes=15)
    assert res.reaped == [] and res.scanned == 0
    assert res.errors and "list_pods failed" in res.errors[0][2]


def test_terminate_error_is_recorded_and_sweep_continues():
    client = FakeClient(
        [_pod("a", "anthill-train-x", 200), _pod("b", "anthill-train-y", 200)],
        fail_terminate=("a",),
    )
    res = reaper.reap_orphaned_pods(client, max_age_minutes=15)
    assert client.terminated == ["b"]  # b still swept after a failed
    assert [r[0] for r in res.reaped] == ["b"]
    assert [e[0] for e in res.errors] == ["a"]


def test_reaper_matches_the_name_the_trainer_builds():
    """Guard: the pod name RunpodTrainer.train() builds must start with the prefix the reaper matches,
    or a real leaked pod would be invisible to the reaper."""
    captured = {}

    class _CapClient:
        def launch(self, *, name, gpu_type, image, disk_gb):
            captured["name"] = name
            return "pid"

        def ssh_host(self, pod_id):
            raise RuntimeError("stop here - we only need the launched name")

        def terminate(self, pod_id):
            pass

    with pytest.raises(RuntimeError):  # _CapClient.ssh_host stops the run right after launch
        RunpodTrainer().train(
            dataset_path="/tmp/x.jsonl", base_model="qwen3:8b", client=_CapClient()
        )
    assert captured["name"].startswith(TRAIN_POD_PREFIX)
    # ...and a pod with that name is reaped.
    client = FakeClient([_pod("p", captured["name"], 999)])
    reaper.reap_orphaned_pods(client, max_age_minutes=15)
    assert client.terminated == ["p"]


def test_real_client_list_pods_parses_graphql_shape(monkeypatch):
    from anthill.training.backends.runpod_train import _RealRunpodPodClient

    c = _RealRunpodPodClient("key")
    monkeypatch.setattr(
        c,
        "_gql",
        lambda q: {
            "myself": {
                "pods": [
                    {
                        "id": "p1",
                        "name": "anthill-train-x",
                        "desiredStatus": "RUNNING",
                        "runtime": {"uptimeInSeconds": 3600},
                    },
                    {"id": "p2", "name": "still-booting", "runtime": None},  # no uptime -> age 0
                    {"name": "no-id-skip", "runtime": {"uptimeInSeconds": 10}},  # dropped
                ]
            }
        },
    )
    pods = c.list_pods()
    assert [p.id for p in pods] == ["p1", "p2"]
    assert pods[0].age_minutes == 60.0 and pods[0].desired_status == "RUNNING"
    assert pods[1].age_minutes == 0.0


def test_trains_on_runpod_detection():
    ns = types.SimpleNamespace
    assert reaper.trains_on_runpod(ns(training_backend="endpoint", training_provider="runpod"))
    assert reaper.trains_on_runpod(ns(training_backend="endpoint", training_provider=""))  # default
    assert not reaper.trains_on_runpod(ns(training_backend="onprem", training_provider="runpod"))
    assert not reaper.trains_on_runpod(ns(training_backend="endpoint", training_provider="modal"))


def test_reaper_client_for_cfg_none_paths(monkeypatch):
    ns = types.SimpleNamespace
    # not a runpod org -> None regardless of key
    assert reaper.reaper_client_for_cfg(ns(training_backend="onprem")) is None
    # runpod org but no key -> None (nothing to authenticate with)
    monkeypatch.setattr("anthill.training.backends.endpoint._runpod_key", lambda cfg: "")
    assert (
        reaper.reaper_client_for_cfg(ns(training_backend="endpoint", training_provider="runpod"))
        is None
    )
    # runpod org with a key -> a real client
    monkeypatch.setattr("anthill.training.backends.endpoint._runpod_key", lambda cfg: "sk-xyz")
    client = reaper.reaper_client_for_cfg(
        ns(training_backend="endpoint", training_provider="runpod")
    )
    assert client is not None and hasattr(client, "list_pods")


def test_configured_run_max_min_clamps():
    ns = types.SimpleNamespace
    assert reaper.configured_run_max_min(ns()) == 120  # default when unset
    assert reaper.configured_run_max_min(ns(aws_max_runtime_min=300)) == 300
    assert reaper.configured_run_max_min(ns(aws_max_runtime_min=5000)) == 1440  # 24h ceiling
    assert reaper.configured_run_max_min(ns(aws_max_runtime_min="oops")) == 120


def test_scheduler_reap_tick_terminates_and_notifies_admins(tmp_path, monkeypatch):
    """The scheduler wiring: a leaked pod for a RunPod org is terminated, audited, and the org's admin
    is pinged through the notification centre."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from anthill.web import scheduler
    from anthill.web.db import (
        AuditLog,
        Base,
        Notification,
        Organization,
        OrgSettings,
        User,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    db.add(org)
    db.flush()
    admin = User(org_id=org.id, email="admin@a.com", role="admin", active=True)
    db.add_all(
        [admin, OrgSettings(org_id=org.id, training_backend="endpoint", training_provider="runpod")]
    )
    db.commit()
    admin_id, org_id = admin.id, org.id
    db.close()

    fake = FakeClient([_pod("leak", "anthill-train-x", 999)])
    # None = "never swept yet", so the tick sweeps now regardless of the process's monotonic clock (a
    # fresh CI runner has a small monotonic value, so a 0.0 sentinel would leave the ~10min gate closed).
    monkeypatch.setattr(scheduler, "_last_reap_at", None)
    monkeypatch.setattr("anthill.training.reaper.reaper_client_for_cfg", lambda cfg: fake)

    scheduler._reap_tick(eng)

    assert fake.terminated == ["leak"]
    db = sessionmaker(bind=eng)()
    assert db.query(Notification).filter(Notification.user_id == admin_id).count() == 1
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event == "training.pod_reaped", AuditLog.org_id == org_id)
        .count()
        == 1
    )
    db.close()
