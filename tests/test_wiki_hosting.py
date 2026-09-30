"""Wiki hosting choice: an admin picks local (default) vs a private VPC instance on the Cloud & model
page (it follows the org cloud); the choice is stored and reflected on the /backend page."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _admin_client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_settings_records_your_own_backend_url(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    # the Wiki tab records your own backend's URL (Anthill doesn't host it - entering a URL is the signal)
    assert 'name="wiki_vpc_url"' in c.get("/settings/organization/wiki").text
    c.post(  # set the org cloud first (hosting only sticks off neocloud)
        "/settings/organization",
        data={"org_provider": "aws", "org_model": "qwen2.5:7b"},
        follow_redirects=False,
    )
    r = c.post(
        "/settings/organization/wiki",
        data={"wiki_vpc_url": "https://anthill.acme.internal"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 200)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.wiki_hosting == "vpc" and cfg.wiki_vpc_url == "https://anthill.acme.internal"


def test_blank_url_means_this_device(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    c.post("/settings/organization/wiki", data={"wiki_vpc_url": ""}, follow_redirects=False)
    assert app_mod._SessionFactory().query(OrgSettings).first().wiki_hosting == "local"


def test_neocloud_cannot_host_the_backend(tmp_path):
    c, app_mod = _admin_client(tmp_path)
    # the org cloud is a neocloud (RunPod) -> a URL must NOT flip to VPC hosting (train-only)
    c.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B"},
        follow_redirects=False,
    )
    c.post(
        "/settings/organization/wiki",
        data={"wiki_vpc_url": "https://anthill.acme.internal"},
        follow_redirects=False,
    )
    assert app_mod._SessionFactory().query(OrgSettings).first().wiki_hosting == "local"


def test_wiki_tab_says_the_org_cloud_hosts_the_wiki_automatically(tmp_path):
    # The org cloud is the home for model + wiki + tuning; the tab says the wiki rides along on the same
    # cloud automatically (no Backend URL to enter), with the live hand-off gated to launch.
    c, _ = _admin_client(tmp_path)
    # with no org cloud configured yet, the page tells you to set one up first
    assert "Set up an org cloud" in c.get("/settings/organization/wiki").text
    c.post(
        "/settings/organization",
        data={"org_provider": "runpod", "org_model": "Qwen2.5 14B"},
        follow_redirects=False,
    )
    page = c.get("/settings/organization/wiki").text
    assert (
        'name="org_serve_wiki"' in page
    )  # the "host the wiki on my org cloud" toggle (default on)
    assert "on my org cloud" in page
    assert "automatically" in page
    assert (
        "Self-host the backend on your own VM" in page
    )  # the manual VM path is now advanced/secondary
    # the old "neocloud cannot host the wiki / nothing to set up" framing is gone
    assert "nothing to set up here" not in page
    assert "cannot host the always-on backend" not in page


def test_backend_page_reflects_the_hosting_choice(tmp_path):
    c, _ = _admin_client(tmp_path)
    body = c.get("/backend").text  # default
    assert "Wiki hosting" in body and "This device" in body

    c.post(
        "/settings/organization",
        data={"org_provider": "aws", "org_model": "qwen2.5:7b"},
        follow_redirects=False,
    )
    c.post(
        "/settings/organization/wiki",
        data={"wiki_vpc_url": "https://anthill.acme.internal"},
        follow_redirects=False,
    )
    body = c.get("/backend").text
    assert "Private VPC instance" in body and "anthill.acme.internal" in body
