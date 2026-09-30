"""The cost/energy per-query rates moved off Settings onto the Metrics page (they tune the
estimated-savings figures, not org config). The Settings form reorders Your Cloud above the
cautionary Hybrid cloud fallback and ends in one full-width sticky Save bar."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _clients(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    admin = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    member = User(org_id=o.id, email="m@a.com", role="member", active=True)
    s.add_all([admin, member])
    s.flush()
    s.add(OrgSettings(org_id=o.id, cost_per_query_usd="0.004", energy_per_query_gco2="4.0"))
    s.commit()
    admin_c = TestClient(app_mod.app)
    admin_c.cookies.set("session_token", make_token(admin.id, o.id, "admin"))
    member_c = TestClient(app_mod.app)
    member_c.cookies.set("session_token", make_token(member.id, o.id, "member"))
    return admin_c, member_c, app_mod, o.id


def test_metrics_page_has_rate_editor_for_admin(tmp_path):
    admin_c, member_c, _, _ = _clients(tmp_path)
    page = admin_c.get("/metrics").text
    assert 'name="cost_per_query_usd"' in page and 'name="energy_per_query_gco2"' in page
    assert "Save rates" in page
    # members see the figures but not the editor
    mpage = member_c.get("/metrics").text
    assert 'name="cost_per_query_usd"' not in mpage
    assert "An admin sets the per-query rates" in mpage


def test_estimates_post_updates_rates(tmp_path):
    admin_c, _, app_mod, org_id = _clients(tmp_path)
    r = admin_c.post(
        "/metrics/estimates",
        data={"cost_per_query_usd": "0.012", "energy_per_query_gco2": "9.5"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "/metrics" in r.headers["location"]
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.cost_per_query_usd == "0.012" and cfg.energy_per_query_gco2 == "9.5"


def test_member_cannot_edit_estimates(tmp_path):
    _, member_c, app_mod, org_id = _clients(tmp_path)
    r = member_c.post(
        "/metrics/estimates",
        data={"cost_per_query_usd": "9.99", "energy_per_query_gco2": "1.0"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 403)  # admin-only route turns members away
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.cost_per_query_usd == "0.004"  # unchanged


def test_settings_dropped_cost_energy_and_reordered(tmp_path):
    admin_c, _, _, _ = _clients(tmp_path)
    page = admin_c.get("/settings").text
    # the cost/energy card is gone from Settings (it now lives on Metrics)
    assert "Cost &amp; energy estimates" not in page and "Cost & energy estimates" not in page
    assert 'name="cost_per_query_usd"' not in page
    # the training/backend-hosting card moved off Settings (training -> Training data, hosting -> Cloud
    # & model); the local-inference card stays. The paid Hybrid cloud fallback card was retired (the code
    # is kept; see test_hybrid_retired).
    assert "Training &amp; backend hosting" not in page
    assert "Local inference" in page
    assert "Hybrid cloud fallback" not in page
    # the Save section is one full-width sticky bar
    assert "settings-savebar" in page
