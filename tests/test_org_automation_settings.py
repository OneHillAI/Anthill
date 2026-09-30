"""Org-behaviour knobs (proactivity, scheduled interval) live on a dedicated Automation page under
the Org hub, not on the this-device /settings page (docs/specs/org-automation-settings.md).

wiki_auto_promote was removed (#683 companion fix-batch): it was a rendered, stored toggle no code
ever read - there was no auto-promotion logic to gate, only the review-queue path."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add_all(
        [
            User(org_id=org.id, email="a@a.com", role="admin", active=True),
            OrgSettings(org_id=org.id),
        ]
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(s.query(User).first().id, org.id, "admin"))
    return c, app_mod, org.id


def test_automation_page_has_the_org_behaviour_knobs(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    page = c.get("/settings/automation").text
    assert 'name="proactivity_mode"' in page
    assert 'name="agent_interval_secs"' in page
    assert 'name="wiki_auto_promote"' not in page  # removed - see module docstring


def test_automation_post_persists_both(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    c.post(
        "/settings/automation",
        data={"proactivity_mode": "scheduled", "agent_interval_secs": "600"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.proactivity_mode == "scheduled"
    assert cfg.agent_interval_secs == 600


def test_automation_coerces_invalid_proactivity(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    c.post("/settings/automation", data={"proactivity_mode": "bogus"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.proactivity_mode == "event"  # invalid -> event


def test_settings_page_no_longer_has_the_moved_knobs(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    page = c.get("/settings").text
    assert 'name="proactivity_mode"' not in page
    assert 'name="agent_interval_secs"' not in page
    assert 'name="wiki_auto_promote"' not in page
    assert 'name="ollama_url"' in page  # still the this-device inference page


def test_saving_settings_does_not_reset_the_automation_knobs(tmp_path, monkeypatch):
    # /settings no longer writes the moved knobs, so saving the inference page must not clobber them.
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    c.post("/settings/automation", data={"proactivity_mode": "scheduled"}, follow_redirects=False)
    c.post(
        "/settings",
        data={"ollama_url": "http://x:11434", "cache_threshold": "0.9"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.proactivity_mode == "scheduled"  # preserved


def test_org_hub_links_to_automation(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    assert 'href="/settings/automation"' in c.get("/settings/org").text
