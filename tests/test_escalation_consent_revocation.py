"""Closing the consent-revocation gap surfaced from a live chat session (#2 of 4 UX questions asked
about the escalation-offer feature): once a human clicks "Always" (OrgSettings.escalation_consented),
there was no way to turn it back off short of clearing the whole provider attachment - and switching
to a DIFFERENT provider silently carried the old consent over to the new one, even though "Always" was
only ever granted for the specific provider named in the chat-runtime disclosure at the time.

Covers both halves of the fix:
  - _apply_escalation_attachment resets escalation_consented when the provider changes or is cleared,
    but leaves it alone on a plain re-save of the SAME provider (the common "just change the mode" case).
  - POST /personalize/escalation-consent-revoke is the missing explicit "turn it off" control - it only
    ever clears the flag, mirroring that granting it is chat-runtime-only (never settable to True here).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch, *, escalation_provider="berget", consented=True):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import encrypt, make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
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
    s.add(
        OrgSettings(
            org_id=o.id,
            deployment_topology="solo",
            escalation_provider=escalation_provider,
            escalation_provider_key_enc=encrypt("secret") if escalation_provider else "",
            escalation_mode="ask",
            escalation_consented=consented,
        )
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_switching_the_escalation_provider_resets_consent(tmp_path, monkeypatch):
    # The founder's "Always" click on Berget must not silently also cover Groq once the account
    # switches attachments - each provider gets its own fresh disclosure.
    c, app_mod = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=True)
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_new",
            "escalation_mode": "ask",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "groq"
    assert cfg.escalation_consented is False


def test_clearing_the_escalation_attachment_resets_consent(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=True)
    c.post(
        "/personalize/compute",
        data={"compute": "local", "escalation_provider": "", "escalation_mode": "ask"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == ""
    assert cfg.escalation_consented is False


def test_resaving_the_same_provider_leaves_consent_alone(tmp_path, monkeypatch):
    # A plain re-save (e.g. just flipping ask/automated) must not reset a consent that still applies to
    # the same provider - only an actual provider change or clear should do that.
    c, app_mod = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=True)
    c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "berget",
            "escalation_mode": "automated",
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "berget"
    assert cfg.escalation_mode == "automated"
    assert cfg.escalation_consented is True


def test_revoke_route_turns_off_an_active_consent(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=True)
    resp = c.post("/personalize/escalation-consent-revoke")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_consented is False
    # the provider attachment itself is untouched - this only clears the "skip asking" flag
    assert cfg.escalation_provider == "berget"


def test_revoke_route_has_no_way_to_grant_consent(tmp_path, monkeypatch):
    # Settings can only ever turn this off; there is deliberately no form field or route argument that
    # sets it to True - granting happens exclusively via the chat runtime's explicit "Always" click.
    c, app_mod = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=False)
    resp = c.post("/personalize/escalation-consent-revoke", data={"escalation_consented": "true"})
    assert resp.status_code == 200
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_consented is False


def test_settings_shows_the_turn_off_control_only_when_consented(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=True)
    body = c.get("/personalize").text
    assert "cc-consent-status" in body
    assert "ccRevokeConsent" in body
    assert 'id="cc-consent-status">' in body  # rendered visible (not hidden) when consented


def test_settings_hides_the_turn_off_control_when_not_consented(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, escalation_provider="berget", consented=False)
    body = c.get("/personalize").text
    assert 'id="cc-consent-status" hidden>' in body
