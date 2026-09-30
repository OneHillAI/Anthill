"""MCP OAuth (hosted connectors): the discovery/DCR/PKCE/token logic (mocked httpx), the Bearer
header builder with refresh, and the start/callback routes (mocked network)."""

import hashlib
import time

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web import mcp_oauth as mo
from anthill.web.db import MCPServer, Organization, User

AS = "https://as.test"
SRV = "https://srv.test"
MCP = SRV + "/mcp"


def _mock_client(routes):
    """An httpx.Client whose responses come from a {(method, path): (status, json|headers)} map."""

    def handler(request: httpx.Request):
        spec = routes.get((request.method, request.url.path))
        if spec is None:
            return httpx.Response(404)
        status, payload = spec
        if isinstance(payload, dict) and "_headers" in payload:
            return httpx.Response(status, headers=payload["_headers"])
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _discovery_routes():
    return {
        ("POST", "/mcp"): (
            401,
            {
                "_headers": {
                    "www-authenticate": f'Bearer resource_metadata="{SRV}/.well-known/oauth-protected-resource/mcp"'
                }
            },
        ),
        ("GET", "/.well-known/oauth-protected-resource/mcp"): (
            200,
            {"authorization_servers": [AS], "resource": MCP},
        ),
        ("GET", "/.well-known/oauth-authorization-server"): (
            200,
            {
                "authorization_endpoint": AS + "/authorize",
                "token_endpoint": AS + "/token",
                "registration_endpoint": AS + "/register",
            },
        ),
    }


# ── the OAuth dance (mocked network) ────────────────────────────────────────────


def test_discover_resolves_endpoints():
    with _mock_client(_discovery_routes()) as c:
        meta = mo.discover(c, MCP)
    assert meta["authorization_endpoint"] == AS + "/authorize"
    assert meta["token_endpoint"] == AS + "/token"
    assert meta["registration_endpoint"] == AS + "/register"
    assert meta["resource"] == MCP


def test_register_client():
    routes = {("POST", "/register"): (201, {"client_id": "cid-123", "client_secret": ""})}
    with _mock_client(routes) as c:
        reg = mo.register_client(c, AS + "/register", "http://localhost:8000/cb")
    assert reg["client_id"] == "cid-123"


def test_pkce_is_valid_s256():
    import base64

    v, ch = mo.new_pkce()
    expect = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert ch == expect


def test_build_authorize_url_has_pkce_and_resource():
    meta = {"authorization_endpoint": AS + "/authorize", "resource": MCP}
    url = mo.build_authorize_url(
        meta,
        client_id="cid",
        redirect_uri="http://localhost:8000/cb",
        state="st",
        code_challenge="cc",
    )
    assert url.startswith(AS + "/authorize?")
    assert "code_challenge=cc" in url and "code_challenge_method=S256" in url
    assert "resource=" in url and "state=st" in url


def test_exchange_and_refresh_set_expiry():
    routes = {
        ("POST", "/token"): (
            200,
            {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600},
        )
    }
    with _mock_client(routes) as c:
        tok = mo.exchange_code(
            c,
            AS + "/token",
            code="x",
            redirect_uri="http://localhost:8000/cb",
            client_id="cid",
            code_verifier="v",
            resource=MCP,
        )
    assert tok["access_token"] == "AT" and tok["expires_at"] > int(time.time())
    with _mock_client(routes) as c:
        ref = mo.refresh_tokens(c, AS + "/token", refresh_token="RT", client_id="cid", resource=MCP)
    assert ref["refresh_token"] == "RT"  # carried over


def test_is_expired():
    assert mo.is_expired({"expires_at": int(time.time()) - 5})
    assert not mo.is_expired({"expires_at": int(time.time()) + 100})


# ── the Bearer header builder (mcp_store.request_headers) ────────────────────────


def test_request_headers_adds_bearer_and_refreshes(monkeypatch):
    from anthill.web import mcp_store

    monkeypatch.setattr(mcp_store, "decrypt_headers", lambda s: {})
    # fresh token -> just attach it
    monkeypatch.setattr(
        mcp_store,
        "_decrypt_json",
        lambda blob: (
            {"access_token": "AT", "expires_at": int(time.time()) + 999} if blob == "tokens" else {}
        ),
    )
    srv = type("S", (), {"oauth_tokens_enc": "tokens", "oauth_client_enc": "", "url": MCP})()
    assert mcp_store.request_headers(None, srv)["Authorization"] == "Bearer AT"

    # expired token with a refresh token + client cfg -> refresh is attempted, new token attached
    state = {"tok": {"access_token": "OLD", "refresh_token": "RT", "expires_at": 1}}
    monkeypatch.setattr(
        mcp_store,
        "_decrypt_json",
        lambda blob: (
            state["tok"]
            if blob == "tok"
            else {"token_endpoint": AS + "/token", "client_id": "cid", "resource": MCP}
        ),
    )
    monkeypatch.setattr(
        mo,
        "refresh_tokens",
        lambda *a, **k: {"access_token": "NEW", "expires_at": int(time.time()) + 999},
    )
    monkeypatch.setattr(mcp_store, "encrypt", lambda v: "enc", raising=False)
    srv2 = type("S", (), {"oauth_tokens_enc": "tok", "oauth_client_enc": "cfg", "url": MCP})()

    class _DB:
        def commit(self):
            pass

    assert mcp_store.request_headers(_DB(), srv2)["Authorization"] == "Bearer NEW"


# ── the routes ──────────────────────────────────────────────────────────────────


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
    srv = MCPServer(
        org_id=o.id, name="Notion", catalog_id="notion", transport="http", url=MCP, status="pending"
    )
    s.add(srv)
    s.commit()
    # loopback base URL: OAuth redirect URIs must be HTTPS unless on localhost
    c = TestClient(app_mod.app, base_url="http://localhost:8000")
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id, srv.id


def test_oauth_start_redirects_to_consent(tmp_path, monkeypatch):
    c, app_mod, _org, sid = _admin_client(tmp_path)
    monkeypatch.setattr(
        mo,
        "discover",
        lambda client, url: {
            "authorization_endpoint": AS + "/authorize",
            "token_endpoint": AS + "/token",
            "registration_endpoint": AS + "/register",
            "resource": MCP,
        },
    )
    monkeypatch.setattr(
        mo, "register_client", lambda *a, **k: {"client_id": "cid", "client_secret": ""}
    )
    r = c.get(f"/mcp/servers/{sid}/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(AS + "/authorize?") and "code_challenge=" in loc
    assert any(p["sid"] == sid for p in app_mod._OAUTH_PENDING.values())  # pending flow recorded


def test_oauth_callback_stores_tokens(tmp_path, monkeypatch):
    c, app_mod, org_id, sid = _admin_client(tmp_path)
    app_mod._OAUTH_PENDING["st8"] = {
        "sid": sid,
        "org_id": org_id,
        "code_verifier": "v",
        "token_endpoint": AS + "/token",
        "resource": MCP,
        "client_id": "cid",
        "client_secret": "",
        "redirect_uri": "http://localhost:8000/connectors/mcp/oauth/callback",
        "ts": time.time(),
    }
    monkeypatch.setattr(
        mo,
        "exchange_code",
        lambda *a, **k: {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_at": int(time.time()) + 999,
        },
    )
    r = c.get("/connectors/mcp/oauth/callback?code=abc&state=st8", follow_redirects=False)
    assert r.status_code == 302
    assert "saved=connected" in r.headers["location"]
    s = app_mod._SessionFactory()
    row = s.query(MCPServer).filter(MCPServer.id == sid).one()
    assert row.oauth_tokens_enc  # tokens persisted (encrypted)
    assert "st8" not in app_mod._OAUTH_PENDING  # consumed


def test_oauth_callback_denied(tmp_path):
    c, _app, _org, _sid = _admin_client(tmp_path)
    r = c.get(
        "/connectors/mcp/oauth/callback?error=access_denied&state=nope", follow_redirects=False
    )
    assert r.status_code == 302
    assert "error=oauth_denied" in r.headers["location"]
