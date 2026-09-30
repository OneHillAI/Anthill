"""The org-plane client: `anthill chat --org` talks to a running Anthill server over HTTP.

OrgClient is unit-tested against an httpx.MockTransport (no real server): form login captures the
session cookie, /chat/new yields a conversation id, and the SSE stream is parsed into token/slug/error
events. The CLI wiring is tested with OrgClient faked, so the dispatch + REPL streaming are covered
without a network.
"""

import httpx
import pytest

from anthill.web_client import OrgClient, OrgClientError


def _client(handler):
    return OrgClient("http://srv", transport=httpx.MockTransport(handler))


def test_login_success_captures_session_cookie():
    def h(req):
        assert req.url.path == "/login"
        assert b"email=" in req.content  # form-encoded credentials
        return httpx.Response(
            302, headers={"location": "/", "set-cookie": "session_token=abc; HttpOnly"}
        )

    c = _client(h)
    c.login("a@a.com", "pw")  # no raise
    assert c._client.cookies.get("session_token") == "abc"


def test_login_failure_raises():
    c = _client(lambda req: httpx.Response(401))
    with pytest.raises(OrgClientError):
        c.login("a@a.com", "bad")


def test_new_conversation_parses_the_redirect_id():
    def h(req):
        assert req.url.path == "/chat/new"
        return httpx.Response(302, headers={"location": "/chat/42"})

    assert _client(h).new_conversation(plane="org") == 42


def test_stream_yields_tokens_and_slugs_and_stops_at_done():
    body = (
        'data: {"token": "Hel"}\n\n'
        'data: {"token": "lo"}\n\n'
        'data: {"meta": {"wiki_slugs": ["db"]}}\n\n'
        "data: [DONE]\n\n"
        'data: {"token": "ignored after done"}\n\n'
    )

    def h(req):
        assert req.url.path == "/chat/7/stream"
        assert req.url.params.get("message") == "hi"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    events = list(_client(h).stream(7, "hi"))
    assert events == [("token", "Hel"), ("token", "lo"), ("slugs", ["db"])]


def test_stream_passes_escalate_org_param():
    def h(req):
        assert req.url.params.get("escalate_org") == "true"
        return httpx.Response(200, text="data: [DONE]\n\n")

    list(_client(h).stream(7, "hi", escalate_org=True))


def test_stream_defaults_escalate_org_false():
    def h(req):
        assert req.url.params.get("escalate_org") == "false"
        return httpx.Response(200, text="data: [DONE]\n\n")

    list(_client(h).stream(7, "hi"))


def test_stream_non_200_raises():
    c = _client(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(OrgClientError):
        list(c.stream(1, "hi"))


# ── CLI wiring (`anthill chat --org`) ────────────────────────────────────────────────────────────


class _FakeOrg:
    def __init__(self, base, **k):
        self.base = base

    def login(self, email, password):
        self.creds = (email, password)

    def new_conversation(self, *, plane="org"):
        return 7

    def stream(self, conv_id, message, *, web=False):
        yield ("token", "Hel")
        yield ("token", "lo")
        yield ("slugs", ["db"])


def test_chat_org_streams_against_the_server(monkeypatch):
    from typer.testing import CliRunner

    import anthill.cli as cli
    import anthill.web_client as wc

    monkeypatch.setattr(wc, "OrgClient", _FakeOrg)
    monkeypatch.setenv("ANTHILL_ORG_URL", "http://srv")
    monkeypatch.setenv("ANTHILL_EMAIL", "a@a.com")
    monkeypatch.setenv("ANTHILL_PASSWORD", "pw")

    r = CliRunner().invoke(cli.app, ["chat", "--org"], input="hi\n")
    assert r.exit_code == 0, r.output
    assert "Hello" in r.output  # streamed tokens reach stdout
    assert "context: db" in r.output  # grounding slugs shown


def test_chat_org_without_server_url_errors(monkeypatch):
    from typer.testing import CliRunner

    import anthill.cli as cli

    monkeypatch.delenv("ANTHILL_ORG_URL", raising=False)
    r = CliRunner().invoke(cli.app, ["chat", "--org"])
    assert r.exit_code == 2  # no server URL -> clean exit before any prompt
