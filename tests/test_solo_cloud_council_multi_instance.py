"""Solo's "Your cloud" (RunPod/Lambda) tier, multi-model council: _apply_solo_compute's cloud branch
now provisions a full council (2-3 models) instead of just the lead. Index 0 (the lead) keeps using
the existing singleton-column path (_start_cloud_provision -> provision_org, stubbed here - it's
already covered by tests/test_provision_ui.py); each reviewer (index 1+) gets its own
org_council_members[i] slot and its own provision_council_member call, threaded so all N members
provision together instead of one at a time.
"""

import json
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import app as app_mod
from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings


def _eng(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def _ctx(tmp_path):
    """A real (db, org, cfg) triple, matching what _apply_solo_compute's callers pass in."""
    eng = _eng(tmp_path)
    Session = sessionmaker(bind=eng)
    db = Session()
    org = Organization(name="A", slug="a")
    db.add(org)
    db.flush()
    cfg = OrgSettings(org_id=org.id)
    db.add(cfg)
    db.commit()
    return db, org, cfg


class _SyncThread:
    """Runs the target synchronously on .start() instead of actually threading - makes the
    fire-and-forget provisioning threads _apply_solo_compute starts deterministic to assert on."""

    def __init__(self, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def _no_lead_provision(monkeypatch, *, started=True):
    """Stub the lead's own provisioning kickoff (already covered by test_provision_ui.py) so these
    tests only exercise the NEW multi-instance wiring, not a real thread/network call."""
    monkeypatch.setattr(app_mod, "_start_cloud_provision", lambda org_id: started)


def test_two_model_council_builds_members_and_provisions_reviewer(tmp_path, monkeypatch):
    db, org, cfg = _ctx(tmp_path)
    _no_lead_provision(monkeypatch)
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    calls = []

    def _fake_provision_member(eng, org_id, member_index, **kw):
        calls.append((org_id, member_index, kw.get("expected_provider"), kw.get("expected_model")))
        return "provisioned"

    import anthill.web.provision_run as provision_run_mod

    monkeypatch.setattr(provision_run_mod, "provision_council_member", _fake_provision_member)

    result = app_mod._apply_solo_compute(
        db,
        org,
        cfg,
        compute="cloud",
        cloud_provider="runpod",
        provider_api_key="test-key",
        council=["qwen3.5:9b", "gemma3:4b"],
        lead="qwen3.5:9b",
    )
    assert result == "provisioning"

    members = json.loads(cfg.org_council_members)
    assert len(members) == 2
    assert members[0]["provider"] == "runpod" and members[0]["model"] == "qwen3.5:9b"
    assert members[1]["provider"] == "runpod" and members[1]["model"] == "gemma3:4b"
    assert members[0]["params_b"] == "9.0" and members[1]["params_b"] == "4.0"
    assert members[0]["lifecycle"] == "vpc" and members[1]["lifecycle"] == "vpc"
    assert members[0]["gpu_tier"] and members[1]["gpu_tier"]  # a real GPU tier was picked for both
    assert members[0]["backend_status"] == "planned"
    assert members[1]["backend_status"] == "planned"

    # Only the reviewer (index 1) goes through provision_council_member - the lead (index 0) stays on
    # the existing singleton-column path (_start_cloud_provision, stubbed above).
    assert calls == [(org.id, 1, "runpod", "gemma3:4b")]


def test_three_model_council_provisions_both_reviewers(tmp_path, monkeypatch):
    db, org, cfg = _ctx(tmp_path)
    _no_lead_provision(monkeypatch)
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    calls = []

    def _fake_provision_member(eng, org_id, member_index, **kw):
        calls.append(member_index)
        return "provisioned"

    import anthill.web.provision_run as provision_run_mod

    monkeypatch.setattr(provision_run_mod, "provision_council_member", _fake_provision_member)

    app_mod._apply_solo_compute(
        db,
        org,
        cfg,
        compute="cloud",
        cloud_provider="lambda",
        provider_api_key="test-key",
        council=["qwen3.5:9b", "gemma3:4b", "phi4:14b"],
        lead="qwen3.5:9b",
    )

    members = json.loads(cfg.org_council_members)
    assert [m["model"] for m in members] == ["qwen3.5:9b", "gemma3:4b", "phi4:14b"]
    assert sorted(calls) == [1, 2]  # both reviewers, never the lead


def test_single_model_cloud_choice_is_unchanged(tmp_path, monkeypatch):
    """A single-model "Your cloud" pick (the pre-existing, already-shipped behavior) must not start
    building a council or touch org_council_members at all - only len(council) >= 2 is new."""
    db, org, cfg = _ctx(tmp_path)
    _no_lead_provision(monkeypatch)
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    import anthill.web.provision_run as provision_run_mod

    def _boom(*a, **kw):
        raise AssertionError("provision_council_member should not run for a single-model choice")

    monkeypatch.setattr(provision_run_mod, "provision_council_member", _boom)

    result = app_mod._apply_solo_compute(
        db,
        org,
        cfg,
        compute="cloud",
        cloud_provider="runpod",
        provider_api_key="test-key",
        council=["qwen3.5:9b"],
        lead="qwen3.5:9b",
    )
    assert result == "provisioning"
    assert cfg.org_model == "qwen3.5:9b"
    assert cfg.org_model_params == "9.0"
    assert (
        cfg.org_council_members == "[]"
    )  # untouched (schema default), exactly like before this change


def test_reviewer_provisioning_is_skipped_when_lead_provision_never_starts(tmp_path, monkeypatch):
    """cloud_pending (no live provisioner / no key) must not silently skip building the council list -
    the reviewers should still be attempted so their own status reflects reality (provision_council_member
    does its own error handling per member); only the RETURN CODE reflects the lead's own outcome."""
    db, org, cfg = _ctx(tmp_path)
    _no_lead_provision(monkeypatch, started=False)
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    calls = []
    import anthill.web.provision_run as provision_run_mod

    monkeypatch.setattr(
        provision_run_mod,
        "provision_council_member",
        lambda *a, **kw: calls.append(a[2]) or "error",
    )

    result = app_mod._apply_solo_compute(
        db,
        org,
        cfg,
        compute="cloud",
        cloud_provider="runpod",
        provider_api_key="test-key",
        council=["qwen3.5:9b", "gemma3:4b"],
        lead="qwen3.5:9b",
    )
    assert result == "cloud_pending"
    assert calls == [1]
