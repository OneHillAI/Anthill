"""Off-LAN remote access: a provider-agnostic secure tunnel (Cloudflare / manual).

The pure command-building + URL-parsing and the TunnelManager (with an injected fake
subprocess) are tested directly; the routes are tested through the app. No real
cloudflared runs.
"""

import io
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.remote import tunnel
from anthill.web import db as db_mod

# ── pure: command + URL parsing ─────────────────────────────────────────────────


def test_build_cmd_quick_vs_named():
    assert tunnel.build_cloudflared_cmd(8000) == [
        "cloudflared",
        "tunnel",
        "--no-autoupdate",
        "--url",
        "http://localhost:8000",
    ]
    named = tunnel.build_cloudflared_cmd(8000, token="abc123")
    assert named[-3:] == ["run", "--token", "abc123"]


def test_parse_public_url():
    out = "INF +-----+\nINF |  https://happy-tree-42.trycloudflare.com  |\nINF +-----+\n"
    assert tunnel.parse_public_url(out) == "https://happy-tree-42.trycloudflare.com"
    # cloudflare's own hosts are skipped; a real tunnel host wins
    skip = "INF connecting to https://api.trycloudflare.com\nINF url=https://my.example.com\n"
    assert tunnel.parse_public_url(skip) == "https://my.example.com"
    assert tunnel.parse_public_url("nothing here") is None


# ── manager (fake subprocess) ───────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, lines):
        self.stderr = io.StringIO("".join(lines))
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False


def test_manager_off_and_manual():
    m = tunnel.TunnelManager()
    assert m.start("off", 8000)["running"] is False
    st = m.start("manual", 8000)
    assert st["running"] is False and st["provider"] == "manual"


def test_manager_cloudflare_captures_url_then_stops():
    m = tunnel.TunnelManager()
    lines = ["INF starting\n", "INF |  https://calm-sky-9.trycloudflare.com  |\n", "INF up\n"]
    st = m.start("cloudflare", 8000, spawn=lambda cmd: _FakeProc(lines))
    assert st["running"] is True
    url = ""
    for _ in range(100):  # let the reader thread pick up the URL
        url = m.status()["url"]
        if url:
            break
        time.sleep(0.01)
    assert url == "https://calm-sky-9.trycloudflare.com"
    m.stop()
    assert m.status()["running"] is False and m.status()["url"] == ""


def test_manager_cloudflare_missing_binary_is_graceful():
    m = tunnel.TunnelManager()
    st = m.start("cloudflare", 8000)  # no spawn injected; cloudflared not installed in CI
    if not tunnel.cloudflared_available():
        assert st["running"] is False and "not installed" in st["detail"]


# ── routes ──────────────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id, "member": member.id}


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_remote_page_and_manual_save(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    assert client.get("/settings/remote").status_code == 200
    r = client.post(
        "/settings/remote",
        data={"remote_access_provider": "manual", "remote_access_url": "https://anthill.acme.com"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    from anthill.web.db import OrgSettings

    cfg = (
        app_mod._SessionFactory()
        .query(OrgSettings)
        .filter(OrgSettings.org_id == ids["org"])
        .first()
    )
    assert (
        cfg.remote_access_provider == "manual"
        and cfg.remote_access_url == "https://anthill.acme.com"
    )
    # the status endpoint reports the manual URL
    st = client.get("/settings/remote/status").json()
    assert st["provider"] == "manual" and st["url"] == "https://anthill.acme.com"


def test_remote_requires_admin(tmp_path, monkeypatch):
    client, _m, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    assert client.get("/settings/remote", follow_redirects=False).status_code == 403
    assert client.post("/settings/remote/stop", follow_redirects=False).status_code == 403
