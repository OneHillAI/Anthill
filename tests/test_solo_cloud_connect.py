"""Bring-your-own-endpoint: a Solo user connects and validates their OWN cloud model server from
personal Settings (never the org "Cloud & model" page). It reuses the org endpoint fields
(org_model_endpoint / org_model / org_model_key_enc / org_backend_status) that plane_routing already
reads for a Solo-cloud turn, keeps deployment_topology == "solo", and never makes the account read as
an org.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch, *, topology="solo"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="Acme Org", slug="acme")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology=topology)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def _stub_validate(monkeypatch, *, ok):
    """Stub the network validation so tests never hit an endpoint."""
    from anthill.hosting import endpoint as ep_mod

    def fake_validate(ep, **kwargs):
        detail = "answered" if ok else "connection refused"
        return ep_mod.Validation(
            ok=ok, checks=[ep_mod.Check(name="reachable", ok=ok, detail=detail)]
        )

    monkeypatch.setattr(ep_mod, "validate", fake_validate)


def test_personalize_byo_endpoint_lives_under_advanced_setups(tmp_path, monkeypatch):
    # After the 3-tier chooser landed, bring-your-own-endpoint (a Server URL + key) is an Advanced
    # option, not the main cloud flow. The main flow is: pick a provider, paste an API key. The
    # /personalize/cloud route (below) still exists and works for when Advanced ships.
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Advanced setups" in body  # the chooser's advanced section
    assert "Self-hosted server or device" in body  # BYO endpoint lives here now
    assert 'action="/personalize/compute"' in body  # the main flow posts the tier choice


def test_connect_validates_and_writes_endpoint_fields(tmp_path, monkeypatch):
    from anthill.web.crypto import decrypt

    c, app_mod = _client(tmp_path, monkeypatch)
    _stub_validate(monkeypatch, ok=True)
    r = c.post(
        "/personalize/cloud",
        data={
            "action": "connect",
            "endpoint_url": "https://gpu.example.com/v1",
            "cloud_model": "qwen2.5:72b",
            "api_key": "sk-secret",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "cloud=ok" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_model_endpoint == "https://gpu.example.com/v1"
    assert cfg.org_model == "qwen2.5:72b"
    assert cfg.solo_compute == "cloud"
    assert cfg.org_backend_status == "validated"
    assert cfg.org_model_key_enc and decrypt(cfg.org_model_key_enc) == "sk-secret"
    # it stays a Solo account - connecting an endpoint is not "becoming an org"
    assert cfg.deployment_topology == "solo"


def test_connect_failure_marks_error_but_keeps_choice(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    _stub_validate(monkeypatch, ok=False)
    r = c.post(
        "/personalize/cloud",
        data={"action": "connect", "endpoint_url": "https://down.example.com/v1"},
        follow_redirects=False,
    )
    assert "cloud=fail" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_backend_status == "error"
    assert (
        cfg.org_model_endpoint == "https://down.example.com/v1"
    )  # saved so the user can fix + retry
    assert cfg.solo_compute == "cloud"  # their choice is kept


def test_disconnect_reverts_to_local(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    _stub_validate(monkeypatch, ok=True)
    c.post(
        "/personalize/cloud",
        data={"action": "connect", "endpoint_url": "https://gpu.example.com/v1"},
        follow_redirects=False,
    )
    r = c.post("/personalize/cloud", data={"action": "disconnect"}, follow_redirects=False)
    assert "cloud=off" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_model_endpoint == ""
    assert cfg.org_backend_status == "unconfigured"
    assert cfg.solo_compute == "local"


def test_connected_endpoint_actually_routes_a_solo_turn(tmp_path, monkeypatch):
    # The payoff: once connected+validated, plane_routing sends a Solo turn to that endpoint.
    from anthill.web.crypto import decrypt
    from anthill.web.plane_routing import plane_inference

    c, app_mod = _client(tmp_path, monkeypatch)
    _stub_validate(monkeypatch, ok=True)
    c.post(
        "/personalize/cloud",
        data={
            "action": "connect",
            "endpoint_url": "https://gpu.example.com/v1",
            "cloud_model": "qwen2.5:72b",
            "api_key": "sk-secret",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    pi = plane_inference("solo", cfg, decrypt=decrypt)
    assert pi.backend == "openai"
    assert pi.base_url == "https://gpu.example.com/v1"
    assert pi.model == "qwen2.5:72b"
    assert pi.wiki_scope == "personal"  # still grounded in the personal wiki, not the org wiki


def test_connected_solo_account_keeps_solo_nav_label(tmp_path, monkeypatch):
    # nav_org is narrowed to genuine org topology, so a connected Solo account never sprouts an org
    # label - the footer still reads the app version (Solo's non-org branch), not an org name.
    from anthill import __version__

    c, _ = _client(tmp_path, monkeypatch)
    _stub_validate(monkeypatch, ok=True)
    c.post(
        "/personalize/cloud",
        data={"action": "connect", "endpoint_url": "https://gpu.example.com/v1"},
        follow_redirects=False,
    )
    body = c.get("/personalize").text
    assert 'class="brand-org"' not in body  # no org name banner in the sidebar
    assert f"v{__version__}" in body  # the footer still reads the version, not an org name


def test_org_topology_is_sent_to_the_admin_page(tmp_path, monkeypatch):
    # A genuine multi-user org configures its shared backend on the admin page, not here.
    c, app_mod = _client(tmp_path, monkeypatch, topology="org")
    _stub_validate(monkeypatch, ok=True)
    r = c.post(
        "/personalize/cloud",
        data={"action": "connect", "endpoint_url": "https://gpu.example.com/v1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/settings/organization"
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_model_endpoint == ""  # unchanged; the solo route refused to act
