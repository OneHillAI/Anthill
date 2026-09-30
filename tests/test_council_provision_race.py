"""Regression coverage for the org_council_members write race: provision_org/teardown_org (the lead,
via singleton columns + _mirror_lead_into_council) and provision_council_member/teardown_council_member
(a reviewer, via its own org_council_members[i] slot) each hold their own DB session open for the
entire duration of a real provisioning/teardown call. Solo's cloud-council wiring triggers a lead
provision and one or more reviewer provisions AT THE SAME TIME (multi-instance "Your cloud" council),
so two of these calls racing to completion within the same few-minute window is the expected case, not
a tail risk. Without the refresh-before-merge fix in provision_run.py, whichever call commits LAST wins
with a stale full-list overwrite that silently erases the other side's already-committed result - these
tests reproduce that interleaving deterministically (no real threads/sleep needed) by having one call's
fake client trigger the OTHER call mid-flight, from inside the first call's own "network" callback."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting.endpoint import Check, Validation
from anthill.hosting.runpod_provision import RunpodDeploy
from anthill.web import db as db_mod
from anthill.web.app import _empty_member
from anthill.web.db import Organization, OrgSettings
from anthill.web.provision_run import provision_council_member, provision_org

_OK = Validation(True, [Check("round_trip", True, "ok")])


def _member(**kw) -> dict:
    return {**_empty_member(), **kw}


def _eng(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    return eng


def _seed(eng, *, lead_singleton: dict, members: list[dict]) -> int:
    """Seed BOTH the lead's legacy singleton columns (provision_org reads these) and
    org_council_members (provision_council_member reads this) - mirroring how _apply_solo_compute's
    cloud branch writes both at once before triggering every member's provisioning."""
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, org_council_members=json.dumps(members), **lead_singleton))
    s.commit()
    return org.id


def _members(eng, org_id):
    cfg = sessionmaker(bind=eng)().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return json.loads(cfg.org_council_members)


class _FakeClient:
    """A minimal RunPod-shaped client whose create_serverless_endpoint can run an arbitrary callback
    before returning - used to splice a concurrent provision call into the middle of this client's own
    "provisioning", standing in for the real network latency that makes two independently,
    simultaneously-triggered live provisions actually overlap in production."""

    def __init__(self, endpoint_id, *, before_ready=None):
        self.endpoint_id = endpoint_id
        self._before_ready = before_ready

    def create_serverless_endpoint(self, **kw):
        if self._before_ready:
            self._before_ready()
        return RunpodDeploy(
            endpoint_id=self.endpoint_id,
            base_url=f"https://api.runpod.ai/v2/{self.endpoint_id}/openai/v1",
        )

    def endpoint_ready(self, endpoint_id):
        return True

    def delete_endpoint(self, endpoint_id):
        pass


def test_reviewer_provision_survives_a_concurrent_lead_provision(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(
        eng,
        lead_singleton={
            "org_provider": "runpod",
            "org_model": "qwen2.5:32b",
            "org_model_params": "32",
        },
        members=[
            _member(provider="runpod", model="qwen2.5:32b", params_b="32"),
            _member(provider="runpod", model="qwen2.5:7b", params_b="7"),
        ],
    )

    def _run_lead_provision_mid_flight():
        status = provision_org(
            eng,
            org_id,
            client=_FakeClient("ep-lead"),
            validate=lambda ep: _OK,
            sleep=lambda s: None,
        )
        assert status == "provisioned"

    status = provision_council_member(
        eng,
        org_id,
        1,
        client=_FakeClient("ep-reviewer", before_ready=_run_lead_provision_mid_flight),
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    assert status == "provisioned"

    members = _members(eng, org_id)
    assert members[0]["backend_status"] == "provisioned"
    assert members[0]["backend_handle"] == "ep-lead"
    assert members[1]["backend_status"] == "provisioned"
    assert members[1]["backend_handle"] == "ep-reviewer"


def test_lead_provision_survives_a_concurrent_reviewer_provision(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(
        eng,
        lead_singleton={
            "org_provider": "runpod",
            "org_model": "qwen2.5:32b",
            "org_model_params": "32",
        },
        members=[
            _member(provider="runpod", model="qwen2.5:32b", params_b="32"),
            _member(provider="runpod", model="qwen2.5:7b", params_b="7"),
        ],
    )

    def _run_reviewer_provision_mid_flight():
        status = provision_council_member(
            eng,
            org_id,
            1,
            client=_FakeClient("ep-reviewer"),
            validate=lambda ep: _OK,
            sleep=lambda s: None,
        )
        assert status == "provisioned"

    status = provision_org(
        eng,
        org_id,
        client=_FakeClient("ep-lead", before_ready=_run_reviewer_provision_mid_flight),
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    assert status == "provisioned"

    members = _members(eng, org_id)
    assert members[0]["backend_status"] == "provisioned"
    assert members[0]["backend_handle"] == "ep-lead"
    assert members[1]["backend_status"] == "provisioned"
    assert members[1]["backend_handle"] == "ep-reviewer"


def test_two_reviewer_provisions_survive_each_other(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(
        eng,
        lead_singleton={
            "org_provider": "runpod",
            "org_model": "qwen2.5:32b",
            "org_model_params": "32",
        },
        members=[
            _member(
                provider="runpod",
                model="qwen2.5:32b",
                params_b="32",
                backend_status="provisioned",
            ),
            _member(provider="runpod", model="qwen2.5:7b", params_b="7"),
            _member(provider="runpod", model="qwen2.5:14b", params_b="14"),
        ],
    )

    def _run_other_reviewer_mid_flight():
        status = provision_council_member(
            eng,
            org_id,
            2,
            client=_FakeClient("ep-r2"),
            validate=lambda ep: _OK,
            sleep=lambda s: None,
        )
        assert status == "provisioned"

    status = provision_council_member(
        eng,
        org_id,
        1,
        client=_FakeClient("ep-r1", before_ready=_run_other_reviewer_mid_flight),
        validate=lambda ep: _OK,
        sleep=lambda s: None,
    )
    assert status == "provisioned"

    members = _members(eng, org_id)
    assert members[0]["backend_status"] == "provisioned"  # untouched by either reviewer call
    assert members[1]["backend_status"] == "provisioned"
    assert members[1]["backend_handle"] == "ep-r1"
    assert members[2]["backend_status"] == "provisioned"
    assert members[2]["backend_handle"] == "ep-r2"
