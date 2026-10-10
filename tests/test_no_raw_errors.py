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
