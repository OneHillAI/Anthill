"""MCP OAuth (client side): connect a hosted MCP server that requires OAuth, no manual app.

Hosted MCP servers (Notion, Sentry, GitHub-remote, ...) advertise an OAuth flow via the
standards the MCP spec builds on: RFC 9728 (protected-resource metadata) -> RFC 8414
(authorization-server metadata) -> RFC 7591 (dynamic client registration), with PKCE. That
means Anthill can register itself and connect with zero setup from the admin - one consent
click, no OAuth app to create (unlike Google/Microsoft, which require a manual client).

This module is web-native and synchronous on purpose: each step is a plain httpx call, so the
flow maps cleanly onto a start route (redirect the admin to consent) and a callback route
(exchange the code), with no background-thread bridge. Every function takes an `httpx.Client`
so the network boundary is injectable for tests.

Token use + refresh live in `mcp_store` (it builds the request headers); storage is the
encrypted `oauth_client_enc` / `oauth_tokens_enc` columns on `MCPServer`.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

_WWW_RESOURCE = re.compile(r'resource_metadata="([^"]+)"')


def new_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


def _resource_metadata_url(client: httpx.Client, mcp_url: str) -> str:
    """The protected-resource-metadata URL: from the server's 401 WWW-Authenticate if it gives
    one, else the RFC 9728 well-known (with the resource path, then at the root)."""
    try:
        r = client.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        m = _WWW_RESOURCE.search(r.headers.get("www-authenticate", ""))
        if m:
            return m.group(1)
    except Exception:
        pass
    p = urlparse(mcp_url)
    return f"{p.scheme}://{p.netloc}/.well-known/oauth-protected-resource{p.path}"


def _auth_server_metadata(client: httpx.Client, auth_server: str) -> dict[str, Any]:
    base = auth_server.rstrip("/")
    for wk in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        try:
            r = client.get(base + wk)
            if r.status_code == 200:
                return dict(r.json())
        except Exception:
            continue
    raise OAuthError(f"no authorization-server metadata at {auth_server}")


class OAuthError(Exception):
    pass


def discover(client: httpx.Client, mcp_url: str) -> dict[str, str]:
    """Resolve a hosted MCP server's OAuth endpoints. Returns authorization_endpoint,
    token_endpoint, registration_endpoint (may be ''), and the canonical resource URI."""
    rm_url = _resource_metadata_url(client, mcp_url)
    try:
        pm = client.get(rm_url).json()
    except Exception as e:
        raise OAuthError(f"protected-resource metadata unavailable: {e}") from e
    servers = pm.get("authorization_servers") or []
    if not servers:
        raise OAuthError("server advertises no authorization_servers")
    md = _auth_server_metadata(client, servers[0])
    if not md.get("authorization_endpoint") or not md.get("token_endpoint"):
        raise OAuthError("authorization-server metadata missing endpoints")
    return {
        "authorization_endpoint": str(md["authorization_endpoint"]),
        "token_endpoint": str(md["token_endpoint"]),
        "registration_endpoint": str(md.get("registration_endpoint") or ""),
        "resource": str(pm.get("resource") or mcp_url),
    }


def register_client(
    client: httpx.Client,
    registration_endpoint: str,
    redirect_uri: str,
    *,
    client_name: str = "Anthill",
) -> dict[str, str]:
    """Dynamic client registration (RFC 7591). Returns {client_id, client_secret}."""
    if not registration_endpoint:
        raise OAuthError("server does not support dynamic client registration")
    body = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    r = client.post(registration_endpoint, json=body)
    if r.status_code not in (200, 201):
        raise OAuthError(f"client registration failed ({r.status_code}): {r.text[:160]}")
    d = r.json()
    if not d.get("client_id"):
        raise OAuthError("registration returned no client_id")
    return {"client_id": str(d["client_id"]), "client_secret": str(d.get("client_secret") or "")}


def build_authorize_url(
    meta: dict[str, str],
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str = "",
) -> str:
    """The URL to send the admin's browser to for consent (PKCE S256 + resource indicator)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": meta["resource"],
    }
    if scope:
        params["scope"] = scope
    return meta["authorization_endpoint"] + "?" + urlencode(params)


def _token_request(
    client: httpx.Client, token_endpoint: str, data: dict[str, str]
) -> dict[str, Any]:
    r = client.post(token_endpoint, data=data, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise OAuthError(f"token endpoint error ({r.status_code}): {r.text[:160]}")
    tok = dict(r.json())
    if not tok.get("access_token"):
        raise OAuthError("token response had no access_token")
    expires_in = int(tok.get("expires_in") or 3600)
    tok["expires_at"] = int(time.time()) + max(60, expires_in - 30)  # refresh a little early
    return tok


def exchange_code(
    client: httpx.Client,
    token_endpoint: str,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    resource: str,
    client_secret: str = "",
) -> dict[str, Any]:
    """Authorization-code -> tokens. Returns the token set with an added `expires_at`."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return _token_request(client, token_endpoint, data)


def refresh_tokens(
    client: httpx.Client,
    token_endpoint: str,
    *,
    refresh_token: str,
    client_id: str,
    resource: str,
    client_secret: str = "",
) -> dict[str, Any]:
    """Refresh an expired access token. Returns the new token set (carry over the refresh token
    if the server does not return a new one)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    if client_secret:
        data["client_secret"] = client_secret
    tok = _token_request(client, token_endpoint, data)
    tok.setdefault("refresh_token", refresh_token)
    return tok


def is_expired(tokens: dict[str, Any]) -> bool:
    return int(tokens.get("expires_at") or 0) <= int(time.time())
