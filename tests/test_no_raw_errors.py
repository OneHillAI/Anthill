"""A member or an MCP caller gets a fixed message when something fails, not the exception text
(docs/specs/no-raw-error-text.md). The detail goes to the server log. The exception text can carry a path
on the server, so each test raises an error that names one and requires that it reaches the log and not
the response. Model-free.
"""

import logging
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.mcp import client as mcp_client
from anthill.mcp import server as mcp_server
from anthill.multimodal import files
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import Organization, OrgSettings, User

SERVER_PATH = "/Users/operator/secret-folder/data.db"


def _cfg():
    return types.SimpleNamespace(
        mcp_server_enabled=True,
        mcp_expose_wiki=True,
        mcp_expose_cache=False,
        mcp_expose_memory=False,
    )


# ── MCP server and client ───────────────────────────────────────────────────────


def test_a_failed_mcp_tool_call_returns_a_fixed_message_and_logs_the_detail(caplog):
    def boom(kind, query):
        raise RuntimeError(f"cannot open {SERVER_PATH}")

    name = mcp_server.RESOURCES["wiki"][0]
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }
    with caplog.at_level(logging.ERROR):
        out = mcp_server.handle_jsonrpc(payload, _cfg(), run_tool=boom)
    assert out["error"]["message"] == "Tool failed."
    assert SERVER_PATH not in str(out)
    assert SERVER_PATH in caplog.text


def test_a_failed_agent_tool_call_returns_a_fixed_message_and_logs_the_detail(caplog):
    def boom(name, args):
        raise RuntimeError(f"cannot open {SERVER_PATH}")

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "agent_tool", "arguments": {}},
    }
    with caplog.at_level(logging.ERROR):
        out = mcp_server.handle_jsonrpc(
            payload, _cfg(), run_tool=boom, a2a_tools=[{"name": "agent_tool"}], a2a_call=boom
        )
    assert out["error"]["message"] == "Tool failed."
    assert SERVER_PATH not in str(out) and SERVER_PATH in caplog.text


def test_a_bad_request_to_an_agent_tool_keeps_its_reason():
    def bad(name, args):
        raise mcp_server.ToolInvalid("'goal' is required")

    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "t", "arguments": {}},
    }
    out = mcp_server.handle_jsonrpc(
        payload, _cfg(), run_tool=bad, a2a_tools=[{"name": "t"}], a2a_call=bad
    )
    assert out["error"]["code"] == -32602
    assert out["error"]["message"] == "Invalid params: 'goal' is required"


def test_an_internal_value_error_or_permission_error_is_not_passed_on(caplog):
    for exc in (
        ValueError("Expecting ',' delimiter: line 1"),
        PermissionError(13, "Permission denied", SERVER_PATH),
    ):

        def boom(name, args, exc=exc):
            raise exc

        payload = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "t", "arguments": {}},
        }
        with caplog.at_level(logging.ERROR):
            out = mcp_server.handle_jsonrpc(
                payload, _cfg(), run_tool=boom, a2a_tools=[{"name": "t"}], a2a_call=boom
            )
        assert out["error"]["message"] == "Tool failed."
        assert SERVER_PATH not in str(out)


def test_a_refusal_from_our_own_check_keeps_its_reason():
    def deny(name, args):
        raise mcp_server.ToolDenied("scope 'files.read' not granted")

    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "t", "arguments": {}},
    }
    out = mcp_server.handle_jsonrpc(
        payload, _cfg(), run_tool=deny, a2a_tools=[{"name": "t"}], a2a_call=deny
    )
    assert "not granted" in out["error"]["message"]


def test_a_failed_outbound_mcp_call_returns_a_fixed_message_and_logs_the_detail(
    monkeypatch, caplog
):
    def boom(coro):
        coro.close()
        raise OSError(f"connection to {SERVER_PATH} refused")

    monkeypatch.setattr(mcp_client, "_run_sync", boom)
    with caplog.at_level(logging.ERROR):
        out = mcp_client.call_tool_raw(object(), {}, "lookup", {})
    assert out == "MCP call failed (lookup)."
    assert SERVER_PATH in caplog.text


def test_a_remote_servers_own_error_message_passes_through(monkeypatch):
    class McpError(
        Exception
    ):  # the SDK's class of this name carries the remote server's JSON-RPC error
        def __init__(self, message):
            super().__init__(f"{message} (local detail {SERVER_PATH})")
            self.error = types.SimpleNamespace(message=message)

    def boom(coro):
        coro.close()
        raise McpError("quota exceeded for this key")

    monkeypatch.setattr(mcp_client, "_run_sync", boom)
    out = mcp_client.call_tool_raw(object(), {}, "lookup", {})
    assert out == "MCP call failed (lookup): quota exceeded for this key"
    assert SERVER_PATH not in out


# ── document preview and export ─────────────────────────────────────────────────


def test_a_failed_preview_shows_a_fixed_notice_and_logs_the_detail(monkeypatch, tmp_path, caplog):
    def boom(path, max_rows):
        raise ValueError(f"bad workbook at {SERVER_PATH}")

    monkeypatch.setattr(files, "_preview_xlsx", boom)
    target = tmp_path / "sheet.xlsx"
    target.write_bytes(b"x")
    with caplog.at_level(logging.ERROR):
        html = files.preview_html(target)
    assert "Preview unavailable" in html and SERVER_PATH not in html
    assert SERVER_PATH in caplog.text


@pytest.fixture
def member(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path / "files"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="m@a.com", role="member", active=True)
    s.add_all([user, OrgSettings(org_id=org.id, escalation_provider="groq")])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    yield client
    s.close()


def test_a_failed_document_export_gives_a_fixed_message_and_logs_the_detail(
    member, monkeypatch, caplog
):
    def boom(*a, **k):
        raise OSError(f"[Errno 28] No space left on device: '{SERVER_PATH}'")

    monkeypatch.setattr(files, "create", boom)
    with caplog.at_level(logging.ERROR):
        r = member.post("/chat/download", data={"content": "# Hello", "format": "pdf"})
    assert r.status_code == 500
    assert r.json() == {"error": "could not create the document"}
    assert SERVER_PATH in caplog.text


def test_a_missing_office_library_gives_a_fixed_message(member, monkeypatch, caplog):
    def missing(*a, **k):
        raise ImportError(f"No module named 'docx' (looked in {SERVER_PATH})")

    monkeypatch.setattr(files, "create", missing)
    with caplog.at_level(logging.ERROR):
        r = member.post("/chat/download", data={"content": "# Hello", "format": "docx"})
    assert r.status_code == 500 and SERVER_PATH not in r.text
    assert "optional library" in r.json()["error"]
    assert SERVER_PATH in caplog.text


# ── provider model discovery (any member) ───────────────────────────────────────


def test_a_failed_model_discovery_gives_a_fixed_message_and_logs_the_detail(
    member, monkeypatch, caplog
):
    from anthill.hosting import endpoint as ep_mod

    def boom(base_url, api_key):
        raise OSError(f"tls failure reading {SERVER_PATH}")

    monkeypatch.setattr(ep_mod, "list_models", boom)
    with caplog.at_level(logging.WARNING):
        r = member.post(
            "/settings/escalation/discover-models",
            data={"escalation_provider": "groq", "escalation_api_key": "k"},
        )
    body = r.json()
    assert body["ok"] is False and SERVER_PATH not in str(body)
    assert "Couldn't list the models" in body["error"]
    assert SERVER_PATH in caplog.text


@pytest.mark.parametrize(
    ("make_error", "expected"),
    [
        (lambda: _status_error(401), "did not accept the key"),
        (lambda: _status_error(403), "did not accept the key"),
        (lambda: _status_error(429), "rate limiting"),
        (
            lambda: __import__("httpx").ConnectError(f"refused {SERVER_PATH}"),
            "Check the connection",
        ),
        (lambda: __import__("httpx").ReadTimeout(f"slow {SERVER_PATH}"), "Check the connection"),
    ],
)
def test_model_discovery_names_the_kind_of_failure_without_the_detail(
    member, monkeypatch, caplog, make_error, expected
):
    from anthill.hosting import endpoint as ep_mod

    error = make_error()

    def boom(base_url, api_key):
        raise error

    monkeypatch.setattr(ep_mod, "list_models", boom)
    with caplog.at_level(logging.WARNING):
        r = member.post(
            "/settings/escalation/discover-models",
            data={"escalation_provider": "groq", "escalation_api_key": "k"},
        )
    body = r.json()
    assert body["ok"] is False and expected in body["error"]
    assert SERVER_PATH not in str(body) and "Traceback" in caplog.text


def _status_error(code):
    import httpx

    request = httpx.Request("GET", f"https://provider.example/{SERVER_PATH}")
    return httpx.HTTPStatusError(
        f"HTTP {code} for {request.url}",
        request=request,
        response=httpx.Response(code, request=request),
    )


# ── ordinary failures still read clearly ────────────────────────────────────────


def test_an_unsupported_export_format_is_still_named(member):
    r = member.post("/chat/download", data={"content": "x", "format": "exe"})
    assert r.status_code == 400 and "unsupported format" in r.json()["error"]


# ── admin connection checks (docs/specs/bounded-text-and-failure-messages-followup.md) ─────────────────
# The checks below are admin-only, but the page still gets a fixed message and the error text goes to
# the log. The checks whose tests already live next to their routes are covered there (training, cloud
# key, mailbox, catalog refresh).

MARKER = "SECRET-MARKER-123"


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="admin@a.com", role="admin", active=True)
    cfg = OrgSettings(org_id=org.id, aws_region="us-east-1", aws_access_key_id="AKIAEXAMPLE")
    s.add_all([user, cfg])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    yield client
    s.close()


def _stored(field):
    return getattr(app_mod._SessionFactory().query(OrgSettings).first(), field)


class _ClientError(Exception):
    """Shaped like botocore's ClientError: AWS answered, with an error code (boto3 is not installed in CI)."""

    def __init__(self, code, text):
        super().__init__(text)
        self.response = {"Error": {"Code": code, "Message": text}}


class _EndpointConnectionError(Exception):
    """Shaped like botocore's EndpointConnectionError (a BotoCoreError): no answer, and a text that quotes
    the endpoint URL."""


def _fake_aws(monkeypatch, error):
    from anthill.cloud import aws

    class _Sts:
        def get_caller_identity(self):
            raise error

    class _Boto:
        @staticmethod
        def client(service, **kw):
            return _Sts()

    monkeypatch.setattr(aws, "boto3", _Boto)
    monkeypatch.setattr(aws, "_secret", lambda cfg: "secret")
    monkeypatch.setattr(aws, "_AWS_REFUSED", (_ClientError,))
    monkeypatch.setattr(aws, "_AWS_UNREACHABLE", (_EndpointConnectionError,))


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            _ClientError("InvalidClientTokenId", f"token invalid at {MARKER}"),
            "AWS rejected the credentials (InvalidClientTokenId).",
        ),
        (
            _EndpointConnectionError(f'Could not connect to the endpoint URL: "https://{MARKER}/"'),
            "Could not reach AWS. Check the region and the connection.",
        ),
        (RuntimeError(f"unexpected {MARKER}"), "Could not check the AWS credentials."),
    ],
    ids=["refused", "unreachable", "other"],
)
def test_the_aws_check_gives_a_fixed_message_per_kind_and_logs_only_the_class(
    admin, monkeypatch, caplog, error, expected
):
    _fake_aws(monkeypatch, error)
    with caplog.at_level(logging.WARNING):
        r = admin.post("/settings/aws/test")
    body = r.json()
    assert set(body) == {"ok", "detail", "status"} and body["ok"] is False
    assert body["detail"].startswith(expected)
    assert MARKER not in r.text and MARKER not in _stored("aws_status_detail")
    assert type(error).__name__ in caplog.text and MARKER not in caplog.text


def test_an_aws_error_code_that_is_not_a_plain_token_is_left_out(admin, monkeypatch):
    _fake_aws(monkeypatch, _ClientError(f"Bad code {MARKER}", "refused"))
    body = admin.post("/settings/aws/test").json()
    assert body["detail"].startswith("AWS rejected the credentials. Check")
    assert MARKER not in body["detail"]


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            _ClientError("InsufficientInstanceCapacity", f"no capacity {MARKER}"),
            "Provision test: AWS could not launch the test machine (InsufficientInstanceCapacity).",
        ),
        (
            _EndpointConnectionError(f'Could not connect to the endpoint URL: "https://{MARKER}/"'),
            "Provision test: Could not reach AWS.",
        ),
        (RuntimeError(f"quota exceeded for {MARKER}"), "Provision test: AWS could not launch"),
    ],
    ids=["refused", "unreachable", "other"],
)
def test_the_aws_provision_test_gives_a_fixed_message_and_logs_only_the_class(
    admin, monkeypatch, caplog, error, expected
):
    from anthill.cloud import aws

    def boom(cfg, purpose=""):
        raise error

    monkeypatch.setattr(aws, "guardrails", lambda cfg: (True, "OK"))
    monkeypatch.setattr(aws, "provision_gpu", boom)
    monkeypatch.setattr(aws, "_AWS_UNREACHABLE", (_EndpointConnectionError,))
    with caplog.at_level(logging.WARNING):
        r = admin.post("/settings/aws/provision-test")
    body = r.json()
    assert set(body) == {"ok", "detail", "status"} and body["ok"] is False
    assert body["detail"].startswith(expected)
    assert MARKER not in r.text and MARKER not in _stored("aws_status_detail")
    assert type(error).__name__ in caplog.text and MARKER not in caplog.text


def test_server_model_discovery_gives_a_fixed_message_and_keeps_the_url_out_of_the_log(
    admin, monkeypatch, caplog
):
    import httpx

    from anthill.hosting import endpoint as ep_mod

    url = "https://models.example/v1?api_key=KEY-IN-URL-456"

    def boom(base_url, api_key):
        request = httpx.Request("GET", base_url)
        raise httpx.HTTPStatusError(
            f"HTTP 401 for {request.url} {MARKER}",
            request=request,
            response=httpx.Response(401, request=request),
        )

    monkeypatch.setattr(ep_mod, "list_models", boom)
    with caplog.at_level(logging.WARNING):
        r = admin.post(
            "/settings/organization/discover-models",
            data={"org_model_endpoint": url, "org_model_key": "k"},
        )
    body = r.json()
    assert set(body) == {"ok", "error"} and body["ok"] is False
    assert body["error"] == "The model server did not accept the key. Check the key and try again."
    assert MARKER not in r.text and "KEY-IN-URL-456" not in r.text
    assert "server model discovery failed" in caplog.text and "401" in caplog.text
    assert "KEY-IN-URL-456" not in caplog.text and MARKER not in caplog.text


@pytest.mark.parametrize("url", ["models.example/v1", "ftp://models.example/v1"])
def test_server_model_discovery_names_the_address_when_it_has_no_web_scheme(admin, url):
    """httpx refuses an address without http:// or https:// before any network call."""
    body = admin.post(
        "/settings/organization/discover-models",
        data={"org_model_endpoint": url, "org_model_key": "k"},
    ).json()
    assert body == {
        "ok": False,
        "error": "Couldn't reach the model server. Check the address (it should start with http:// or https://).",
    }


def test_server_model_discovery_names_the_address_when_it_is_malformed(admin, monkeypatch):
    import httpx

    from anthill.hosting import endpoint as ep_mod

    def boom(base_url, api_key):
        raise httpx.InvalidURL(f"Invalid port: {MARKER}")

    monkeypatch.setattr(ep_mod, "list_models", boom)
    body = admin.post(
        "/settings/organization/discover-models",
        data={"org_model_endpoint": "http://models.example:x/v1", "org_model_key": "k"},
    ).json()
    assert "Check the address (it should start with http:// or https://)." in body["error"]
    assert MARKER not in body["error"]


# ── the org model endpoint check (hosting/endpoint.py validate) ───────────────────────────────────────


def _http_status_error(code, url):
    import httpx

    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"Server error '{code}' for url '{url}' {MARKER}",
        request=request,
        response=httpx.Response(code, request=request),
    )


@pytest.mark.parametrize(
    "fail_at, error, expected",
    [
        (
            "reachable",
            "connect",
            "reachable: Could not reach the server. Check the address and the connection.",
        ),
        (
            "reachable",
            401,
            "reachable: The server did not accept the key. Check the key and try again.",
        ),
        (
            "reachable",
            "address",
            "reachable: Check the address (it should start with http:// or https://).",
        ),
        ("round_trip", 502, "round_trip: The server answered with HTTP 502."),
        (
            "round_trip",
            "other",
            "round_trip: The test chat did not get an answer. Check the model and the server.",
        ),
    ],
)
def test_the_org_endpoint_check_stores_a_fixed_message_without_the_url(
    admin, monkeypatch, caplog, fail_at, error, expected
):
    import httpx

    from anthill.hosting import endpoint as ep_mod

    url = "https://gpu.example/v1?api_key=KEY-IN-URL-456"
    if error == "connect":
        exc = httpx.ConnectError(f"cannot connect to {url} {MARKER}")
    elif error == "address":
        exc = httpx.UnsupportedProtocol(f"Request URL is missing a protocol: {url} {MARKER}")
    elif error == "other":
        exc = KeyError(f"choices {MARKER}")
    else:
        exc = _http_status_error(error, url)

    def fail(*a, **k):
        raise exc

    if fail_at == "reachable":
        monkeypatch.setattr(ep_mod, "_list_models", fail)
    else:
        monkeypatch.setattr(ep_mod, "_list_models", lambda ep: ["m"])
        monkeypatch.setattr(ep_mod, "_round_trip", fail)
    with caplog.at_level(logging.WARNING):
        r = admin.post(
            "/settings/organization/connect",
            data={"org_model_endpoint": url, "org_model_key": "k"},
            follow_redirects=False,
        )
    assert r.status_code == 302 and "connected=fail" in r.headers["location"]
    detail = _stored("org_backend_detail")
    assert detail.endswith(expected)
    assert MARKER not in detail and "KEY-IN-URL-456" not in detail and "gpu.example" not in detail
    assert f"org endpoint check {fail_at} failed: {type(exc).__name__}" in caplog.text
    assert MARKER not in caplog.text and "gpu.example" not in caplog.text
