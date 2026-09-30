"""Phase 1b: provisioning/teardown for a council REVIEWER member (member_index >= 1), via
provision_run.provision_council_member/teardown_council_member. Mirrors test_provision_ui.py's
provision_org/teardown_org coverage but against org_council_members[i] instead of the legacy singleton
columns; the lead (member_index 0, provision_org/teardown_org) is untouched by this file."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import secure_tunnel
from anthill.hosting.endpoint import Check, Validation
from anthill.hosting.runpod_provision import RunpodDeploy
from anthill.web import db as db_mod
from anthill.web.app import _empty_member
from anthill.web.db import Organization, OrgSettings
from anthill.web.provision_run import provision_council_member, teardown_council_member

_OK = Validation(True, [Check("round_trip", True, "ok")])


class _FakeTunnelProc:
    def poll(self):
        return None  # always "running" - these tests do not exercise tunnel failure


def _fake_tunnel_spawn(argv):
    return _FakeTunnelProc()


class _FakeRunpodClient:
    def __init__(self, *, ready_after=1):
        self.ready_after = ready_after
        self._polls = 0
        self.deleted = []

    def create_serverless_endpoint(
        self,
        *,
        name,
        hf_model,
        max_workers,
        idle_seconds,
        gpu_ids="AMPERE_24",
        hf_token="",
        min_workers=0,
    ):
        self.gpu_ids = gpu_ids
        return RunpodDeploy(
            endpoint_id="ep-r1", base_url="https://api.runpod.ai/v2/ep-r1/openai/v1"
        )

    def endpoint_ready(self, endpoint_id):
        self._polls += 1
        return self._polls >= self.ready_after

    def delete_endpoint(self, endpoint_id):
        self.deleted.append(endpoint_id)


def _member(**kw) -> dict:
    return {**_empty_member(), **kw}


def _eng(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def _seed(eng, members: list[dict]):
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, org_council_members=json.dumps(members)))
    s.commit()
    return org.id


def _cfg(eng, org_id):
    return sessionmaker(bind=eng)().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


def _members(eng, org_id):
    return json.loads(_cfg(eng, org_id).org_council_members)


# ── provision_council_member ────────────────────────────────────────────────────────


def test_provision_reviewer_success_persists_endpoint_and_handle(tmp_path):
    eng = _eng(tmp_path)
    lead = _member(provider="lambda", model="qwen2.5:32b")
    reviewer = _member(provider="runpod", model="qwen2.5:7b", params_b="7")
    org_id = _seed(eng, [lead, reviewer])
    status = provision_council_member(
        eng, org_id, 1, client=_FakeRunpodClient(), validate=lambda ep: _OK, sleep=lambda s: None
    )
    assert status == "provisioned"
    members = _members(eng, org_id)
    assert members[1]["backend_status"] == "provisioned"
    assert members[1]["backend_handle"] == "ep-r1"
    assert members[1]["endpoint"] == "https://api.runpod.ai/v2/ep-r1/openai/v1"
    assert members[0]["backend_status"] == "unconfigured"  # the lead is untouched


def test_provision_reviewer_failure_records_error(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member(), _member(provider="runpod", model="qwen2.5:7b", params_b="7")])
    fail = Validation(False, [Check("reachable", False, "down")])
    status = provision_council_member(
        eng, org_id, 1, client=_FakeRunpodClient(), validate=lambda ep: fail, sleep=lambda s: None
    )
    assert status == "error"
    members = _members(eng, org_id)
    assert members[1]["backend_status"] == "error" and "down" in members[1]["backend_detail"]
    assert members[1]["endpoint"] == ""  # nothing half-committed


def test_provision_reviewer_without_plan_errors(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member(), _member()])  # reviewer has no provider/model chosen
    assert provision_council_member(eng, org_id, 1, client=_FakeRunpodClient()) == "error"
    assert _members(eng, org_id)[1]["backend_status"] == "error"


def test_provision_reviewer_out_of_range_index_errors(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member()])  # only a lead, no reviewer at index 1
    assert provision_council_member(eng, org_id, 1, client=_FakeRunpodClient()) == "error"
    assert (
        provision_council_member(eng, org_id, 0, client=_FakeRunpodClient()) == "error"
    )  # index 0 too


def test_provision_reviewer_unexpected_error_is_recorded_not_left_provisioning(
    tmp_path, monkeypatch
):
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member(), _member(provider="runpod", model="qwen2.5:7b", params_b="7")])

    class _Boom:
        key = "runpod"

        def provision(self, spec, **kw):
            raise RuntimeError("kaboom")

    monkeypatch.setattr("anthill.web.provision_run.prov.get_provisioner", lambda p: _Boom())
    status = provision_council_member(eng, org_id, 1, client=object())
    assert status == "error"
    assert "kaboom" in _members(eng, org_id)[1]["backend_detail"]


def test_provision_reviewer_shares_the_account_provision_key_not_a_per_member_one(tmp_path):
    # R3: a single shared credential across members is explicitly allowed - reviewers have no
    # provision_key of their own; the account-level cfg.org_provision_key_enc is what's decrypted.
    from anthill.web.crypto import encrypt

    eng = _eng(tmp_path)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    encrypted_key = encrypt("shared-key")
    s.add(
        OrgSettings(
            org_id=org.id,
            org_provision_key_enc=encrypted_key,
            org_council_members=json.dumps(
                [_member(), _member(provider="runpod", model="qwen2.5:7b", params_b="7")]
            ),
        )
    )
    s.commit()
    org_id = org.id

    captured = {}

    def _fake_live_client(ref):
        captured["key_source"] = ref.org_provision_key_enc
        return _FakeRunpodClient()

    import anthill.web.provision_run as pr

    orig = pr._live_client
    pr._live_client = _fake_live_client
    try:
        provision_council_member(eng, org_id, 1, validate=lambda ep: _OK, sleep=lambda s: None)
    finally:
        pr._live_client = orig
    assert captured["key_source"] == encrypted_key


# ── teardown_council_member ─────────────────────────────────────────────────────────


def test_teardown_reviewer_resets_state(tmp_path):
    eng = _eng(tmp_path)
    lead = _member(provider="lambda", model="qwen2.5:32b", backend_status="planned")
    reviewer = _member(
        provider="runpod",
        model="qwen2.5:7b",
        params_b="7",
        backend_status="provisioned",
        backend_handle="ep-r1",
        endpoint="https://api.runpod.ai/v2/ep-r1/openai/v1",
    )
    org_id = _seed(eng, [lead, reviewer])
    client = _FakeRunpodClient()
    status = teardown_council_member(eng, org_id, 1, client=client)
    assert status == "planned" and client.deleted == ["ep-r1"]
    members = _members(eng, org_id)
    assert members[1]["backend_handle"] == "" and members[1]["endpoint"] == ""
    assert members[0]["backend_status"] == "planned"  # the lead is untouched


def test_teardown_reviewer_with_invalid_provider_keeps_the_handle(tmp_path):
    # A provider that can't even be resolved means teardown was never attempted - the handle must
    # survive so a human can retry or clean up manually, not vanish into "unconfigured".
    eng = _eng(tmp_path)
    reviewer = _member(provider="", backend_status="error", backend_handle="stale-handle")
    org_id = _seed(eng, [_member(), reviewer])
    status = teardown_council_member(eng, org_id, 1, client=_FakeRunpodClient())
    assert status == "error"
    members = _members(eng, org_id)
    assert members[1]["backend_handle"] == "stale-handle"
    assert "not attempted" in members[1]["backend_detail"]


def test_teardown_reviewer_out_of_range_index_errors(tmp_path):
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member()])
    assert teardown_council_member(eng, org_id, 1, client=_FakeRunpodClient()) == "error"


# ── found by the ASDD dev-council review of this file: endpoint credential was discarded ───────────


class _FakeLambdaClient:
    """Comes up active immediately; carries no api_key itself - Lambda's provisioner generates a
    fresh random one per run and puts it on the returned OrgEndpoint (the thing under test here)."""

    def __init__(self):
        self.terminated = []

    def launch(self, *, region, instance_type, ssh_key_names, startup_script, name):
        return "i-review-1"

    def instance(self, instance_id):
        return ("active", "10.0.0.9")

    def terminate(self, instance_id):
        self.terminated.append(instance_id)


def test_provision_reviewer_persists_the_generated_endpoint_key(tmp_path, monkeypatch):
    # Lambda (and DataCrunch) generate a fresh random API key per provision and the served endpoint
    # REQUIRES it; losing it would mean the app can never actually call the endpoint it just stood up.
    from anthill.web.crypto import decrypt

    monkeypatch.delenv("ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT", raising=False)
    eng = _eng(tmp_path)
    lead = _member(provider="lambda", model="qwen2.5:32b")
    reviewer = _member(provider="lambda", model="qwen2.5:7b", params_b="7")
    org_id = _seed(eng, [lead, reviewer])
    status = provision_council_member(
        eng,
        org_id,
        1,
        client=_FakeLambdaClient(),
        sleep=lambda s: None,
        tunnel_spawn=_fake_tunnel_spawn,
        tunnel_manager=secure_tunnel.SSHTunnelManager(),
    )
    assert status == "provisioned"
    members = _members(eng, org_id)
    # Now a supervised SSH tunnel (docs/specs/llm-endpoint-secure-transport.md), not the VM's public IP.
    assert members[1]["endpoint"].startswith("http://127.0.0.1:")
    assert members[1]["endpoint"].endswith("/v1")
    key_enc = members[1]["model_key_enc"]
    assert key_enc  # not discarded
    assert len(decrypt(key_enc)) >= 32  # the real generated secret, round-trips through encryption


# ── found by the ASDD dev-council review: a stale member_index race after reorder/removal ──────────


def test_provision_reviewer_refuses_when_the_snapshot_no_longer_matches(tmp_path):
    # Simulates the race: a settings save changed what's at member_index between the request and this
    # (normally background-threaded) call - the stale snapshot must refuse rather than provision into
    # what is now a different member's slot.
    eng = _eng(tmp_path)
    org_id = _seed(eng, [_member(), _member(provider="runpod", model="qwen2.5:7b", params_b="7")])
    status = provision_council_member(
        eng,
        org_id,
        1,
        client=_FakeRunpodClient(),
        expected_provider="lambda",  # stale: the real member at index 1 is "runpod"
        expected_model="qwen2.5:7b",
    )
    assert status == "error"
    members = _members(eng, org_id)
    assert members[1]["backend_status"] == "unconfigured"  # untouched, no provision was attempted


def test_teardown_reviewer_refuses_when_the_handle_no_longer_matches(tmp_path):
    eng = _eng(tmp_path)
    reviewer = _member(provider="runpod", backend_handle="ep-current")
    org_id = _seed(eng, [_member(), reviewer])
    status = teardown_council_member(
        eng, org_id, 1, client=_FakeRunpodClient(), expected_handle="ep-stale"
    )
    assert status == "error"
    members = _members(eng, org_id)
    assert members[1]["backend_handle"] == "ep-current"  # untouched, no teardown was attempted
