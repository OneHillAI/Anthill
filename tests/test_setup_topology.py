"""Setup wizard topology revision: org (default) vs solo, replacing the retired
'small team - fully local' mode. The field is write-only metadata; these cover the
setup write + the legacy-value normalization used for display.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import OrgSettings, normalize_topology


def test_normalize_topology():
    assert normalize_topology("local") == "solo"  # legacy
    assert normalize_topology("gpu") == "org"  # legacy
    assert normalize_topology("org") == "org"
    assert normalize_topology("solo") == "solo"
    assert normalize_topology("") == "org"


def _fresh_app(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app), app_mod


def test_setup_org_records_backend(tmp_path):
    c, app_mod = _fresh_app(tmp_path)
    try:
        r = c.post(
            "/setup",
            data={
                "org_name": "Acme",
                "admin_email": "a@acme.com",
                "admin_password": "longenoughpw1",
                "admin_name": "A",
                "topology": "org",
                "gpu_backend": "vpc",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        st = app_mod._SessionFactory().query(OrgSettings).first()
        assert st.deployment_topology == "org"
        assert st.training_backend == "vpc" and st.gpu_cloud == "aws"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_setup_org_records_neocloud_backend(tmp_path):
    """The setup wizard must offer the neocloud (own RunPod/Modal) backend, not just
    VPC/on-prem - it maps to training_backend='endpoint' (provider/token added in Settings)."""
    c, app_mod = _fresh_app(tmp_path)
    try:
        r = c.post(
            "/setup",
            data={
                "org_name": "Neo Inc",
                "admin_email": "n@neo.com",
                "admin_password": "longenoughpw1",
                "topology": "org",
                "gpu_backend": "endpoint",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        st = app_mod._SessionFactory().query(OrgSettings).first()
        assert st.deployment_topology == "org"
        assert st.training_backend == "endpoint"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_setup_org_onprem_backend(tmp_path):
    c, app_mod = _fresh_app(tmp_path)
    try:
        r = c.post(
            "/setup",
            data={
                "org_name": "Onprem Co",
                "admin_email": "o@onprem.com",
                "admin_password": "longenoughpw1",
                "topology": "org",
                "gpu_backend": "onprem",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        st = app_mod._SessionFactory().query(OrgSettings).first()
        assert st.training_backend == "onprem"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_setup_omitting_topology_creates_a_personal_solo_account(tmp_path):
    # The sign-up form no longer asks about org vs solo (docs/specs/signup-no-org.md): a sign-up that
    # sends only identity + password creates a PERSONAL (solo) account, with no GPU backend configured.
    c, app_mod = _fresh_app(tmp_path)
    try:
        r = c.post(
            "/setup",
            data={"admin_email": "me@example.com", "admin_password": "longenoughpw1"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        st = app_mod._SessionFactory().query(OrgSettings).first()
        assert st.deployment_topology == "solo"  # defaults to solo now, not org
        assert st.aws_status == "unconfigured"  # no org backend set up at sign-up
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_setup_page_has_no_org_or_backend_chooser(tmp_path):
    # The sign-up surface must not present org setup (topology / GPU backend / org name).
    c, _app_mod = _fresh_app(tmp_path)
    page = c.get("/setup").text
    assert 'name="topology"' not in page
    assert 'name="gpu_backend"' not in page
    assert 'name="org_name"' not in page
    assert 'name="admin_email"' in page and 'name="admin_password"' in page  # still a sign-up form


def test_setup_solo_no_backend(tmp_path):
    c, app_mod = _fresh_app(tmp_path)
    try:
        r = c.post(
            "/setup",
            data={
                "org_name": "Solo Eval",
                "admin_email": "s@solo.com",
                "admin_password": "longenoughpw1",
                "topology": "solo",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        st = app_mod._SessionFactory().query(OrgSettings).first()
        assert st.deployment_topology == "solo"
        assert st.aws_status == "unconfigured"  # creating solo needs no backend
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
