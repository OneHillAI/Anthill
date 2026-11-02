"""The web surface for the privacy pack (issue #540): the Settings page shows what the cloud PII scrub
covers and offers a one-click install where possible; the install runs in the background."""

# anthill.hybrid re-exports scrub(), shadowing the submodule name, so patch the real modules.
import importlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User

_scrub = importlib.import_module("anthill.hybrid.scrub")
_pack = importlib.import_module("anthill.hybrid.privacy_pack")


def _admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [User(org_id=o.id, email="a@a.com", role="admin", active=True), OrgSettings(org_id=o.id)]
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    # inline background work so the install path is exercised synchronously
    monkeypatch.setattr(app_mod, "_spawn", lambda target, *a, **k: target(*a, **k))
    return c, app_mod, o.id


def test_settings_shows_the_gap_and_install_button(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: False)
    monkeypatch.setattr(_pack, "can_install", lambda: True)
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/settings").text
    assert "NOT redacted without the privacy pack" in body  # the gap is surfaced
    assert "not redacted yet" not in body  # no duplicated trailing sentence (#155)
    assert 'action="/privacy-pack/install"' in body  # ...with a one-click install


def test_personalize_privacy_scrub_copy_matches_presidio_state(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setattr(_scrub, "presidio_available", lambda: False)
    body_absent = c.get("/personalize").text
    assert "Removes emails, phone numbers and IDs first." in body_absent
    assert "Names and places need the privacy pack" in body_absent
    assert "Removes names, emails and IDs first." not in body_absent
    assert 'href="/audit"' in body_absent  # Solo/admin Privacy tab links to /audit (#156)

    monkeypatch.setattr(_scrub, "presidio_available", lambda: True)
    body_present = c.get("/personalize").text
    assert "Removes names, locations, emails, phone numbers and IDs first." in body_present


def test_solo_dashboard_links_audit_and_audit_labels_utc(tmp_path, monkeypatch):
    from anthill.web import audit as audit_mod

    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).filter_by(org_id=org_id).first()
    cfg.deployment_topology = "solo"
    cfg.local_model_chosen = True
    audit_mod.log(s, "inference.call", "model=qwen2.5:3b", org_id=org_id, user_id=1)
    s.commit()

    dash = c.get("/").text
    assert "<h3>Your workspace</h3>" in dash
    assert 'href="/audit"' in dash
    audit_page = c.get("/audit").text
    assert " UTC" in audit_page and "inference.call" in audit_page


def test_settings_shows_installed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: True)
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/settings").text
    assert "privacy pack installed" in body.lower()
    assert 'action="/privacy-pack/install"' not in body  # nothing to install


def test_settings_frozen_explains_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: False)
    monkeypatch.setattr(_pack, "can_install", lambda: False)
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/settings").text
    assert "Not available in the packaged app" in body
    assert 'action="/privacy-pack/install"' not in body  # no broken button


def test_install_route_runs_when_installable(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: False)
    monkeypatch.setattr(_pack, "can_install", lambda: True)
    calls = {"n": 0}
    monkeypatch.setattr(
        _pack,
        "install",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    c, _, _ = _admin(tmp_path, monkeypatch)
    r = c.post("/privacy-pack/install", follow_redirects=False)
    assert "installing=privacy" in r.headers["location"] and calls["n"] == 1


def test_install_route_is_noop_when_already_available(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: True)
    monkeypatch.setattr(
        _pack,
        "install",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not install")),
    )
    c, _, _ = _admin(tmp_path, monkeypatch)
    r = c.post("/privacy-pack/install", follow_redirects=False)
    assert r.headers["location"] == "/settings"  # already installed -> no-op


def test_status_route_reports_state(tmp_path, monkeypatch):
    monkeypatch.setattr(_scrub, "presidio_available", lambda: False)
    monkeypatch.setattr(_pack, "can_install", lambda: True)
    c, _, _ = _admin(tmp_path, monkeypatch)
    assert c.get("/privacy-pack/status").json() == {
        "installing": False,
        "available": False,
        "can_install": True,
    }
