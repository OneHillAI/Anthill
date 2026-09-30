"""Inbound push events: webhooks (Slack + generic) and IMAP feed the workspace inbox/
and wake the event tick immediately, instead of waiting for the next poll.

Pure verification/parsing is tested directly; IMAP uses a fake client (no live mailbox);
the webhook routes are tested through the app with seeded, encrypted secrets.
"""

import json
import time
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import imap_idle, ingest_push

# ── pure: filenames + inbox writing ────────────────────────────────────────────


def test_inbox_filename_is_sanitized():
    name = ingest_push.inbox_filename("slack", "../../etc/passwd", stamp="20260609T120000")
    assert "/" not in name and "\\" not in name and ".." not in name
    assert name.endswith(".md")


def test_write_event_lands_in_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    p = ingest_push.write_event(inbox, "generic", "A title", "the body", stamp="20260609T120000")
    assert p.parent == inbox.resolve()
    text = p.read_text()
    assert "# A title" in text and "the body" in text


# ── pure: verification ─────────────────────────────────────────────────────────


def test_verify_token_constant_time_match():
    assert ingest_push.verify_token("s3cret", "s3cret") is True
    assert ingest_push.verify_token("s3cret", "nope") is False
    assert ingest_push.verify_token("", "anything") is False


def test_slack_signature_roundtrip_and_replay_guard():
    secret, ts, body = "shh", "1700000000", b'{"event":{}}'
    sig = ingest_push.slack_signature(secret, ts, body)
    assert ingest_push.verify_slack(secret, ts, body, sig, now=1700000000) is True
    assert ingest_push.verify_slack(secret, ts, body, "v0=deadbeef", now=1700000000) is False
    # Stale request (> 5 min skew) is rejected even with a valid signature.
    assert ingest_push.verify_slack(secret, ts, body, sig, now=1700000000 + 999) is False


# ── pure: payload extraction ───────────────────────────────────────────────────


def test_slack_event_text_filters_noise():
    msg = {"event": {"type": "message", "text": "hi team", "channel": "C1"}}
    assert ingest_push.slack_event_text(msg) == ("Slack message in C1", "hi team")
    assert (
        ingest_push.slack_event_text({"event": {"type": "message", "bot_id": "B1", "text": "x"}})
        is None
    )
    assert ingest_push.slack_event_text({"event": {"type": "reaction_added"}}) is None
    assert (
        ingest_push.slack_event_text({"event": {"type": "message", "subtype": "channel_join"}})
        is None
    )


def test_generic_event_text_json_and_raw():
    title, body = ingest_push.generic_event_text(
        b'{"title":"Deploy","text":"shipped v2"}', "application/json"
    )
    assert title == "Deploy" and body == "shipped v2"
    _t2, b2 = ingest_push.generic_event_text(b"plain note", "text/plain")
    assert b2 == "plain note"


def test_email_parse_and_doc():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"Subject: Status update\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"All systems green.\r\n"
    )
    parsed = ingest_push.parse_email_bytes(raw)
    assert parsed["subject"] == "Status update" and "alice@example.com" in parsed["from"]
    title, body = ingest_push.email_to_doc(parsed)
    assert title == "Status update" and "All systems green." in body


# ── IMAP with a fake client (no live mailbox) ──────────────────────────────────


class _FakeIMAP:
    def __init__(self, host, port, *, messages=None, login_ok=True, select_ok=True):
        self.host, self.port = host, port
        self.messages = messages or []
        self.login_ok, self.select_ok = login_ok, select_ok
        self.stored, self.logged_out = [], False

    def login(self, user, password):
        if not self.login_ok:
            raise OSError("auth failed")
        return ("OK", [b"ok"])

    def select(self, folder, readonly=False):
        return ("OK" if self.select_ok else "NO", [b"1"])

    def search(self, charset, *criteria):
        nums = b" ".join(str(i + 1).encode() for i in range(len(self.messages)))
        return ("OK", [nums])

    def fetch(self, num, spec):
        raw = self.messages[int(num) - 1]
        return ("OK", [(b"%s (RFC822)" % num, raw), b")"])

    def store(self, num, flag, value):
        self.stored.append((num, value))

    def logout(self):
        self.logged_out = True


def test_imap_test_connection_paths():
    ok, detail = imap_idle.test_connection(
        "imap.x", 993, "u", "p", "INBOX", factory=lambda h, p: _FakeIMAP(h, p)
    )
    assert ok and "connected" in detail
    bad, _ = imap_idle.test_connection(
        "imap.x", 993, "u", "p", factory=lambda h, p: _FakeIMAP(h, p, login_ok=False)
    )
    assert bad is False
    miss, detail = imap_idle.test_connection("", 993, "", "")
    assert miss is False and "required" in detail


def test_imap_fetch_unseen_marks_seen():
    msgs = [b"From: a@x.com\r\nSubject: one\r\n\r\nbody one\r\n"]
    client = _FakeIMAP("h", 993, messages=msgs)
    out = imap_idle.fetch_unseen(client, "INBOX")
    assert out == msgs and client.stored == [(b"1", "\\Seen")]


def test_imap_drain_once_writes_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "workspace"))
    msgs = [b"From: a@x.com\r\nSubject: hello\r\n\r\nthe content\r\n"]
    cfg = SimpleNamespace(
        imap_host="h", imap_port=993, imap_user="u", imap_password_enc="", imap_folder="INBOX"
    )
    fired = []
    n = imap_idle.drain_once(
        cfg, factory=lambda h, p: _FakeIMAP(h, p, messages=msgs), on_event=lambda: fired.append(1)
    )
    assert n == 1 and fired == [1]
    files = list((tmp_path / "workspace" / "inbox").iterdir())
    assert len(files) == 1 and "the content" in files[0].read_text()


# ── webhook routes ──────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch, *, webhook=True, token="tok-123", slack_secret="shh"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import encrypt
    from anthill.web.db import Organization, OrgSettings

    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "workspace"))
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
    cfg = OrgSettings(
        org_id=org.id,
        webhook_enabled=webhook,
        webhook_secret_enc=encrypt(token) if token else "",
        slack_signing_secret_enc=encrypt(slack_secret) if slack_secret else "",
    )
    admin = db_mod.User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add_all([cfg, admin])
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id}


def _inbox(tmp_path):
    d = tmp_path / "workspace" / "inbox"
    return list(d.iterdir()) if d.is_dir() else []


def test_slack_url_verification_echoes_challenge(tmp_path, monkeypatch):
    client, _m, _ = _app(tmp_path, monkeypatch)
    r = client.post("/webhooks/slack", json={"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json()["challenge"] == "abc123"


def test_slack_valid_signature_ingests(tmp_path, monkeypatch):
    client, _m, _ = _app(tmp_path, monkeypatch, slack_secret="shh")
    payload = {"event": {"type": "message", "text": "ship it", "channel": "C9"}}
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = ingest_push.slack_signature("shh", ts, body)
    r = client.post(
        "/webhooks/slack",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig,
        },
    )
    assert r.status_code == 200 and r.json().get("ok")
    files = _inbox(tmp_path)
    assert len(files) == 1 and "ship it" in files[0].read_text()


def test_slack_bad_signature_rejected(tmp_path, monkeypatch):
    client, _m, _ = _app(tmp_path, monkeypatch, slack_secret="shh")
    body = json.dumps({"event": {"type": "message", "text": "x"}}).encode()
    r = client.post(
        "/webhooks/slack",
        content=body,
        headers={"x-slack-request-timestamp": str(int(time.time())), "x-slack-signature": "v0=bad"},
    )
    assert r.status_code == 401
    assert _inbox(tmp_path) == []


def test_generic_token_ingests_and_rejects(tmp_path, monkeypatch):
    client, _m, _ = _app(tmp_path, monkeypatch, token="tok-123")
    ok = client.post(
        "/webhooks/generic",
        content=b'{"title":"Build","text":"green"}',
        headers={"content-type": "application/json", "x-anthill-webhook-token": "tok-123"},
    )
    assert ok.status_code == 200 and len(_inbox(tmp_path)) == 1
    bad = client.post(
        "/webhooks/generic", content=b"x", headers={"x-anthill-webhook-token": "wrong"}
    )
    assert bad.status_code == 401 and len(_inbox(tmp_path)) == 1  # still just the one


def test_unknown_source_404(tmp_path, monkeypatch):
    client, _m, _ = _app(tmp_path, monkeypatch)
    assert client.post("/webhooks/telegram", content=b"x").status_code == 404


# ── settings/events config ──────────────────────────────────────────────────────


def _auth(client, uid, org_id):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, "admin"))


def test_settings_events_mints_webhook_token(tmp_path, monkeypatch):
    # start with webhooks off + no token; saving with the box checked should mint one
    client, app_mod, ids = _app(tmp_path, monkeypatch, webhook=False, token="", slack_secret="")
    _auth(client, ids["admin"], ids["org"])
    r = client.post("/settings/events", data={"webhook_enabled": "true"}, follow_redirects=False)
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    from anthill.web.db import OrgSettings

    cfg = (
        app_mod._SessionFactory()
        .query(OrgSettings)
        .filter(OrgSettings.org_id == ids["org"])
        .first()
    )
    assert cfg.webhook_enabled and cfg.webhook_secret_enc  # token minted + stored (encrypted)
    from anthill.web.crypto import decrypt

    assert decrypt(cfg.webhook_secret_enc)  # decrypts to a real token


def test_settings_imap_test_requires_config(tmp_path, monkeypatch):
    client, _m, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.post("/settings/events/imap/test")
    body = r.json()
    assert body["ok"] is False and "required" in body["detail"]
