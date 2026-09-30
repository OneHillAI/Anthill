"""#661 Tier 5: the POST /settings/organization/cluster route and its GET-render effects
(_cluster_workers_from_cfg, the estimate/warning/reachability display). Anthill never launches or
manages any rpc-server/llama-server process - this only covers config, estimation, and a raw TCP
reachability display."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.app import _cluster_workers_from_cfg
from anthill.web.db import Organization, OrgSettings, User

# ── pure helper ──────────────────────────────────────────────────────────────────────


def test_cluster_workers_from_cfg_none_and_blank():
    assert _cluster_workers_from_cfg(None) == []

    class _Cfg:
        org_cluster_workers = ""

    assert _cluster_workers_from_cfg(_Cfg()) == []


def test_cluster_workers_from_cfg_bad_json_is_empty():
    class _Cfg:
        org_cluster_workers = "not json"

    assert _cluster_workers_from_cfg(_Cfg()) == []

    class _CfgDict:
        org_cluster_workers = json.dumps({"not": "a list"})

    assert _cluster_workers_from_cfg(_CfgDict()) == []


def test_cluster_workers_from_cfg_parses_real_entries():
    class _Cfg:
        org_cluster_workers = json.dumps(
            [{"label": "box2", "host": "10.0.0.2", "port": "50052", "mem_gb": "64"}]
        )

    workers = _cluster_workers_from_cfg(_Cfg())
    assert workers == [{"label": "box2", "host": "10.0.0.2", "port": "50052", "mem_gb": "64"}]


# ── route tests ──────────────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

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


def _cfg_of(app_mod, org_id):
    return app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


def _set_provider(client, provider):
    return client.post(
        "/settings/organization",
        data={"org_provider": provider},
        follow_redirects=False,
    )


def test_cluster_enable_rejected_when_not_onprem(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "aws")

    r = client.post(
        "/settings/organization/cluster",
        data={"org_cluster_enabled": "on", "worker_count": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=cluster_needs_onprem" in r.headers["location"]
    assert _cfg_of(app_mod, ids["org"]).org_cluster_enabled is False


def test_cluster_enable_accepted_when_onprem(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")

    r = client.post(
        "/settings/organization/cluster",
        data={
            "org_cluster_enabled": "on",
            "org_cluster_kind": "apple",
            "org_cluster_main_mem_gb": "64",
            "worker_count": "0",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "saved=1" in r.headers["location"]
    cfg = _cfg_of(app_mod, ids["org"])
    assert cfg.org_cluster_enabled is True
    assert cfg.org_cluster_kind == "apple"
    assert cfg.org_cluster_main_mem_gb == "64"


def test_provider_switch_away_from_onprem_autoclears_cluster(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")
    client.post(
        "/settings/organization/cluster",
        data={"org_cluster_enabled": "on", "worker_count": "0"},
        follow_redirects=False,
    )
    assert _cfg_of(app_mod, ids["org"]).org_cluster_enabled is True

    _set_provider(client, "aws")
    assert _cfg_of(app_mod, ids["org"]).org_cluster_enabled is False


def test_worker_count_capped_at_three_server_side(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")

    data = {"org_cluster_enabled": "on", "worker_count": "6"}
    for i in range(6):
        data[f"worker_label_{i}"] = f"box{i}"
        data[f"worker_host_{i}"] = f"10.0.0.{i}"
        data[f"worker_port_{i}"] = "50052"
        data[f"worker_mem_gb_{i}"] = "64"
    client.post("/settings/organization/cluster", data=data, follow_redirects=False)

    workers = json.loads(_cfg_of(app_mod, ids["org"]).org_cluster_workers)
    assert len(workers) == 3


def test_worker_rows_missing_host_and_label_are_dropped(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")

    client.post(
        "/settings/organization/cluster",
        data={
            "org_cluster_enabled": "on",
            "worker_count": "2",
            "worker_label_0": "",
            "worker_host_0": "",
            "worker_port_0": "50052",
            "worker_mem_gb_0": "64",
            "worker_label_1": "box1",
            "worker_host_1": "10.0.0.1",
            "worker_port_1": "50052",
            "worker_mem_gb_1": "64",
        },
        follow_redirects=False,
    )
    workers = json.loads(_cfg_of(app_mod, ids["org"]).org_cluster_workers)
    assert len(workers) == 1
    assert workers[0]["host"] == "10.0.0.1"


def test_get_renders_estimate_and_reachability(tmp_path, monkeypatch):
    monkeypatch.setattr("anthill.hosting.cluster.tcp_reachable", lambda h, p, **k: h == "10.0.0.1")
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")
    client.post(
        "/settings/organization/cluster",
        data={
            "org_cluster_enabled": "on",
            "org_cluster_kind": "apple",
            "org_cluster_main_mem_gb": "64",
            "worker_count": "2",
            "worker_label_0": "box1",
            "worker_host_0": "10.0.0.1",
            "worker_port_0": "50052",
            "worker_mem_gb_0": "64",
            "worker_label_1": "box2",
            "worker_host_1": "10.0.0.2",
            "worker_port_1": "50052",
            "worker_mem_gb_1": "64",
        },
        follow_redirects=False,
    )

    body = client.get("/settings/organization").text
    assert "params" in body
    assert "port open" in body
    assert "no response" in body
    assert "server healthy" not in body


def test_get_shows_no_warning_when_cluster_disabled(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")
    body = client.get("/settings/organization").text
    assert "may exceed this cluster's estimated capacity" not in body


def test_get_shows_warning_when_selected_model_exceeds_cluster_estimate(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    _set_provider(client, "onprem")
    client.post(
        "/settings/organization/cluster",
        data={
            "org_cluster_enabled": "on",
            "org_cluster_kind": "apple",
            "org_cluster_main_mem_gb": "16",
            "worker_count": "0",
        },
        follow_redirects=False,
    )
    s = app_mod._SessionFactory()
    row = s.query(OrgSettings).filter(OrgSettings.org_id == ids["org"]).first()
    row.org_model_params = "9999"  # far bigger than a single 16GB node could ever serve
    s.commit()

    body = client.get("/settings/organization").text
    assert "may exceed this cluster's estimated capacity" in body
