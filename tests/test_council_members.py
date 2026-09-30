"""Phase 1: OrgSettings' single-model org_* block -> an ordered org_council_members list.

Covers the pure helpers (app._empty_member / app._members_from_cfg), the version-1 backfill migration,
row isolation between two accounts on the same install, and that the existing single-member
/settings/organization save also mirrors into the new column.
"""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import migrate
from anthill.web.app import _empty_member, _members_from_cfg
from anthill.web.db import Organization, OrgSettings

# ── pure helpers ─────────────────────────────────────────────────────────────────────


def test_empty_member_shape():
    m = _empty_member()
    assert m["provider"] == "" and m["model"] == ""
    assert m["backend_status"] == "unconfigured"
    assert m["lifecycle"] == "vpc"
    assert m["quantized"] is False
    assert m["provider_config"] == {}


def test_members_from_cfg_none_and_blank():
    assert _members_from_cfg(None) == []

    class _Cfg:
        org_council_members = ""

    assert _members_from_cfg(_Cfg()) == []


def test_members_from_cfg_merges_partial_member_onto_defaults():
    class _Cfg:
        org_council_members = json.dumps([{"provider": "lambda", "model": "qwen2.5:32b"}])

    members = _members_from_cfg(_Cfg())
    assert len(members) == 1
    assert members[0]["provider"] == "lambda" and members[0]["model"] == "qwen2.5:32b"
    assert members[0]["backend_status"] == "unconfigured"  # filled in from _empty_member()
    assert members[0]["provider_config"] == {}


def test_members_from_cfg_bad_json_is_empty():
    class _Cfg:
        org_council_members = "not json"

    assert _members_from_cfg(_Cfg()) == []

    class _CfgDict:
        org_council_members = json.dumps({"not": "a list"})

    assert _members_from_cfg(_CfgDict()) == []


# ── version-1 migration (backfill) ──────────────────────────────────────────────────


def _engine_with_org_settings(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mig.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(
        eng
    )  # builds schema incl. org_council_members; stamps to head (no-op: fresh)
    return eng


def test_migration_backfills_unconfigured_row_to_empty_list(tmp_path):
    eng = _engine_with_org_settings(tmp_path)
    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="Blank", slug="blank")
        s.add(org)
        s.flush()
        s.add(OrgSettings(org_id=org.id))
        s.commit()
        org_id = org.id

    with eng.begin() as conn:
        migrate._mig_0001_org_council_members(conn)

    with Session() as s:
        cfg = s.query(OrgSettings).filter_by(org_id=org_id).one()
        assert json.loads(cfg.org_council_members) == []


def test_migration_backfills_configured_row_to_one_member(tmp_path):
    eng = _engine_with_org_settings(tmp_path)
    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="Acme", slug="acme")
        s.add(org)
        s.flush()
        s.add(
            OrgSettings(
                org_id=org.id,
                org_provider="lambda",
                org_model="llama-3.1-70b",
                org_model_params="70",
                org_region="us-east-1",
                org_gpu="80",
                org_model_quantized=True,
                org_model_endpoint="http://1.2.3.4:8000/v1",
                org_backend_handle="i-abc123",
                org_backend_status="provisioned",
                org_lambda_ssh_keys="k1,k2",
                org_lambda_instance_type="gpu_1x_a100",
            )
        )
        s.commit()
        org_id = org.id

    with eng.begin() as conn:
        migrate._mig_0001_org_council_members(conn)

    with Session() as s:
        cfg = s.query(OrgSettings).filter_by(org_id=org_id).one()
        members = json.loads(cfg.org_council_members)
        assert len(members) == 1
        m = members[0]
        assert m["provider"] == "lambda" and m["model"] == "llama-3.1-70b"
        assert m["backend_status"] == "provisioned"  # carried, not reset
        assert m["backend_handle"] == "i-abc123"
        assert m["quantized"] is True
        assert m["provider_config"] == {"ssh_keys": "k1,k2", "instance_type": "gpu_1x_a100"}


def test_migration_is_idempotent_and_never_clobbers_a_real_council(tmp_path):
    eng = _engine_with_org_settings(tmp_path)
    Session = sessionmaker(bind=eng)
    with Session() as s:
        org = Organization(name="Already", slug="already")
        s.add(org)
        s.flush()
        # a real council already present (e.g. a re-run after a partial upgrade) - must survive intact
        s.add(
            OrgSettings(
                org_id=org.id,
                org_provider="lambda",
                org_model="ignored",
                org_council_members=json.dumps([{"provider": "nebius", "model": "custom"}]),
            )
        )
        s.commit()
        org_id = org.id

    with eng.begin() as conn:
        migrate._mig_0001_org_council_members(conn)

    with Session() as s:
        cfg = s.query(OrgSettings).filter_by(org_id=org_id).one()
        members = json.loads(cfg.org_council_members)
        assert len(members) == 1
        assert members[0]["provider"] == "nebius"  # untouched, not overwritten with the lambda row


# ── multi-account independence (personal/local vs. org/VPC on the same install) ────


def test_two_accounts_have_independent_councils(tmp_path):
    eng = _engine_with_org_settings(tmp_path)
    Session = sessionmaker(bind=eng)

    with Session() as s:
        org_a = Organization(name="Personal", slug="personal")
        org_b = Organization(name="VpcOrg", slug="vpc-org")
        s.add_all([org_a, org_b])
        s.flush()
        s.add(OrgSettings(org_id=org_a.id, solo_compute="local", ollama_model="qwen2.5:3b"))
        b_member = {
            **_empty_member(),
            "provider": "lambda",
            "model": "llama-3.1-70b",
            "params_b": "70",
            "region": "us-east-1",
            "gpu_tier": "80",
            "lifecycle": "vpc",
            "backend_status": "provisioned",
            "backend_handle": "lambda-abc123",
            "provider_config": {"ssh_keys": "k1", "instance_type": "gpu_1x_a100"},
        }
        s.add(
            OrgSettings(
                org_id=org_b.id, solo_compute="cloud", org_council_members=json.dumps([b_member])
            )
        )
        s.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    # mutate B; A must be unaffected
    with Session() as s:
        b = s.query(OrgSettings).filter_by(org_id=org_b_id).one()
        members = _members_from_cfg(b)
        members[0]["backend_status"] = "error"
        b.org_council_members = json.dumps(members)
        s.commit()

    with Session() as s:
        a = s.query(OrgSettings).filter_by(org_id=org_a_id).one()
        b = s.query(OrgSettings).filter_by(org_id=org_b_id).one()
        assert _members_from_cfg(a) == []
        assert a.ollama_model == "qwen2.5:3b" and a.solo_compute == "local"
        bm = _members_from_cfg(b)
        assert len(bm) == 1
        assert bm[0]["backend_status"] == "error"
        assert bm[0]["provider_config"]["instance_type"] == "gpu_1x_a100"
        assert a.org_id != b.org_id


# ── /settings/organization save also mirrors into org_council_members ──────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id}


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_settings_save_mirrors_selection_into_council_members(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "org_lambda_ssh_keys": "mykey",
            "org_lambda_instance_type": "gpu_1x_a10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    cfg = (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == ids["org"])
        .first()
    )
    members = _members_from_cfg(cfg)
    assert len(members) == 1
    assert members[0]["provider"] == "lambda" and members[0]["model"] == "qwen2.5:32b"
    assert members[0]["params_b"] == "32"
    assert members[0]["backend_status"] == cfg.org_backend_status == "planned"
    assert members[0]["provider_config"] == {"ssh_keys": "mykey", "instance_type": "gpu_1x_a10"}


def test_settings_save_with_no_provider_resets_lead_slot_to_unconfigured(tmp_path, monkeypatch):
    # Phase 1b: index 0 (the lead slot) always exists once N-member support is real, even blank -
    # unlike Phase 1's interim mirror, which represented "unconfigured" as an empty list.
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "qwen2.5:32b"},
        follow_redirects=False,
    )
    client.post("/settings/organization", data={}, follow_redirects=False)

    cfg = (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == ids["org"])
        .first()
    )
    members = _members_from_cfg(cfg)
    assert len(members) == 1
    assert members[0]["provider"] == "" and members[0]["model"] == ""
    assert members[0]["backend_status"] == "unconfigured"


# ── Phase 1b: reviewer rows on the same save ────────────────────────────────────────


def _cfg_of(app_mod, org_id):
    return (
        app_mod._SessionFactory()
        .query(db_mod.OrgSettings)
        .filter(db_mod.OrgSettings.org_id == org_id)
        .first()
    )


def test_settings_save_persists_lead_and_reviewers(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "2",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
            "reviewer_provider_1": "lambda",
            "reviewer_model_1": "llama3.1:8b",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]

    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert len(members) == 3
    assert members[0]["provider"] == "lambda" and members[0]["model"] == "qwen2.5:32b"
    assert members[1]["provider"] == "runpod" and members[1]["model"] == "qwen2.5:7b"
    assert members[2]["provider"] == "lambda" and members[2]["model"] == "llama3.1:8b"


def test_settings_save_reviewer_custom_model_id(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "__custom__",
            "reviewer_model_custom_0": "my-org/Custom-7B",
        },
        follow_redirects=False,
    )
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert members[1]["model"] == "my-org/Custom-7B"


def test_settings_save_rejects_reviewer_model_too_big_for_its_gpu(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "lambda",
            "reviewer_model_0": "Llama 3.3 70B",
            "reviewer_gpu_0": "24",  # far too small
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=too_big" in r.headers["location"]
    # nothing committed - atomic save across the whole member set
    cfg = _cfg_of(app_mod, ids["org"])
    assert cfg is None or _members_from_cfg(cfg) == []


def test_settings_save_unchanged_reviewer_preserves_its_provisioned_state(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    # simulate the reviewer having been provisioned via its own Provision button
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == ids["org"]).one()
    members = json.loads(cfg.org_council_members)
    members[1]["backend_status"] = "provisioned"
    members[1]["backend_handle"] = "ep-live"
    members[1]["endpoint"] = "https://api.runpod.ai/v2/ep-live/openai/v1"
    cfg.org_council_members = json.dumps(members)
    s.commit()

    # an unrelated re-save (e.g. touching only the lead) must not disturb the reviewer's live status
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert members[1]["backend_status"] == "provisioned"
    assert members[1]["backend_handle"] == "ep-live"


def test_settings_save_refuses_to_orphan_a_removed_provisioned_reviewer(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == ids["org"]).one()
    members = json.loads(cfg.org_council_members)
    members[1]["backend_status"] = "provisioned"
    members[1]["backend_handle"] = "ep-live"
    cfg.org_council_members = json.dumps(members)
    s.commit()

    # save with reviewer_count=0 - the browser removed the reviewer row - while it is still provisioned
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "qwen2.5:32b", "reviewer_count": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=teardown_first" in r.headers["location"]
    # the reviewer survives untouched
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert len(members) == 2 and members[1]["backend_handle"] == "ep-live"


def test_settings_save_removing_an_unprovisioned_reviewer_succeeds(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    r = client.post(
        "/settings/organization",
        data={"org_provider": "lambda", "org_model": "qwen2.5:32b", "reviewer_count": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert len(members) == 1  # the reviewer is gone, lead intact


def test_council_provision_route_dispatches_for_reviewer(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    client.post("/settings/organization/provision-key", data={"org_provision_key": "k"})
    monkeypatch.setattr(
        "anthill.web.provision_run.provision_council_member", lambda *a, **k: "provisioned"
    )
    r = client.post(
        "/settings/organization/council/provision",
        data={"member_index": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "provisioning=started" in r.headers["location"]
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert members[1]["backend_status"] == "provisioning"


def test_council_provision_route_requires_a_key(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    r = client.post(
        "/settings/organization/council/provision",
        data={"member_index": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=no_key" in r.headers["location"]


def test_council_teardown_route_dispatches_for_reviewer(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == ids["org"]).one()
    members = json.loads(cfg.org_council_members)
    members[1]["backend_handle"] = "ep-live"
    cfg.org_council_members = json.dumps(members)
    s.commit()

    monkeypatch.setattr(
        "anthill.web.provision_run.teardown_council_member", lambda *a, **k: "planned"
    )
    r = client.post(
        "/settings/organization/council/teardown",
        data={"member_index": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "torn=1" in r.headers["location"]


def test_council_teardown_route_requires_a_handle(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    r = client.post(
        "/settings/organization/council/teardown",
        data={"member_index": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=nothing" in r.headers["location"]


# ── Phase 1b: provision_org/teardown_org (the lead) mirror into council_members[0] ──


def test_provision_org_mirrors_result_into_council_members_zero(tmp_path):
    from anthill.hosting.endpoint import Check, Validation
    from anthill.hosting.runpod_provision import RunpodDeploy
    from anthill.web.provision_run import provision_org

    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = db_mod.Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(
        db_mod.OrgSettings(
            org_id=org.id, org_provider="runpod", org_model="qwen2.5:7b", org_model_params="7"
        )
    )
    s.commit()
    org_id = org.id

    class _Client:
        def create_serverless_endpoint(self, **kw):
            return RunpodDeploy(
                endpoint_id="ep1", base_url="https://api.runpod.ai/v2/ep1/openai/v1"
            )

        def endpoint_ready(self, endpoint_id):
            return True

        def delete_endpoint(self, endpoint_id):
            pass

    ok = Validation(True, [Check("round_trip", True, "ok")])
    status = provision_org(
        eng, org_id, client=_Client(), validate=lambda ep: ok, sleep=lambda s: None
    )
    assert status == "provisioned"

    members = _members_from_cfg(
        s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == org_id).first()
    )
    assert members[0]["backend_status"] == "provisioned"
    assert members[0]["backend_handle"] == "ep1"
    assert members[0]["endpoint"] == "https://api.runpod.ai/v2/ep1/openai/v1"


# ── found by the ASDD dev-council review: the lead could "steal" a removed reviewer's handle ───────


def test_lead_selection_matching_a_removed_reviewer_still_refuses_the_orphan(tmp_path, monkeypatch):
    # If the lead and a REVIEWER pool were matched by the same content-based pass, a lead whose new
    # selection happens to equal a dropped reviewer's old one would "adopt" that reviewer's still-live
    # handle during carry-forward - which then gets overwritten by the lead's real (blank) legacy
    # columns a few lines later, so the orphan check (which ran before that overwrite) would wrongly
    # let the save through. The lead must only ever carry forward against its own prior slot.
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "qwen2.5:32b",
            "reviewer_count": "1",
            "reviewer_provider_0": "runpod",
            "reviewer_model_0": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == ids["org"]).one()
    members = json.loads(cfg.org_council_members)
    members[1]["backend_status"] = "provisioned"
    members[1]["backend_handle"] = "ep-live"
    cfg.org_council_members = json.dumps(members)
    s.commit()

    # Change the LEAD's selection to exactly match the reviewer's (now-removed) selection.
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "runpod",
            "org_model": "qwen2.5:7b",
            "reviewer_count": "0",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=teardown_first" in r.headers["location"]
    members = _members_from_cfg(_cfg_of(app_mod, ids["org"]))
    assert len(members) == 2 and members[1]["backend_handle"] == "ep-live"  # untouched, not stolen


# ── Phase 2: the SUMMED on-prem council footprint gate (product-council-architecture.md R5) ────────


def test_settings_save_refuses_onprem_council_that_does_not_fit_summed(tmp_path, monkeypatch):
    from anthill.hosting import sizing

    monkeypatch.setattr(
        sizing, "local_hardware", lambda: (40.0, "gpu")
    )  # each fits alone, not summed
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "onprem",
            "org_model": "GLM-4.7-flash",  # 30B
            "reviewer_count": "1",
            "reviewer_provider_0": "onprem",
            "reviewer_model_0": "DeepSeek-R1 32B",  # 32B; summed 4-bit cost exceeds a 40GB gpu box
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=onprem_council_too_big" in r.headers["location"]
    cfg = _cfg_of(app_mod, ids["org"])
    assert cfg is None or _members_from_cfg(cfg) == []  # nothing committed


def test_settings_save_accepts_a_single_onprem_member_that_fits(tmp_path, monkeypatch):
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (40.0, "gpu"))
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "onprem", "org_model": "GLM-4.7-flash"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]


def test_settings_save_never_gates_a_lone_onprem_member_even_if_it_would_not_fit(
    tmp_path, monkeypatch
):
    # Pre-existing, deliberate design (test_post_onprem_skips_the_fit_gate in
    # test_org_provisioning.py): on-prem is the org's own box - the gate only guards GPUs Anthill
    # itself provisions. A lone on-prem member has always saved unchecked; the Phase 2 summed gate
    # must never regress that, even on hardware far too small for the chosen model.
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (4.0, "gpu"))  # tiny box
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={"org_provider": "onprem", "org_model": "DeepSeek V3.1"},  # 671B
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    assert _cfg_of(app_mod, ids["org"]).org_model == "DeepSeek V3.1"


def test_settings_save_vpc_council_unaffected_by_the_onprem_gate(tmp_path, monkeypatch):
    from anthill.hosting import sizing

    # Deliberately a tiny "local machine" - if the VPC path were wrongly summed against it, this would
    # be refused; it must not be, since VPC members each get their own dedicated cloud instance.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (8.0, "gpu"))
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post(
        "/settings/organization",
        data={
            "org_provider": "lambda",
            "org_model": "GLM-4.7-flash",
            "org_gpu": "80",
            "reviewer_count": "1",
            "reviewer_provider_0": "lambda",
            "reviewer_model_0": "DeepSeek-R1 32B",
            "reviewer_gpu_0": "80",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
