"""Branded error pages: a browser navigation to a broken URL gets the warm fleeing-ant page, while
API/JSON clients keep their JSON error body and login redirects still redirect (see app.py handlers
and docs/specs/error-pages.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anthill.web.app import app

HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_404_html_is_branded(client):
    r = client.get("/no-such-page-xyz", headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "You stepped on one of us" in r.text
    assert 'class="escene"' in r.text  # the fleeing-ant scene rendered
    assert "404" in r.text


def test_404_json_is_preserved(client):
    # No text/html in Accept (the default TestClient sends */*) -> the JSON contract is unchanged.
    r = client.get("/no-such-page-xyz")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}


def test_login_redirect_still_redirects(client):
    # A protected page raises HTTPException(303, Location=/login). That must stay a redirect, never
    # become an error page, even for a browser request.
    r = client.get("/settings", headers=HTML, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_500_html_is_branded_json_otherwise(client):
    async def _boom(request):
        raise RuntimeError("kaboom")

    app.add_route("/__boom_test__", _boom)
    try:
        h = client.get("/__boom_test__", headers=HTML)
        assert h.status_code == 500
        assert h.headers["content-type"].startswith("text/html")
        assert "You stepped on one of us" in h.text

        j = client.get("/__boom_test__")
        assert j.status_code == 500
        assert j.json() == {"detail": "Internal Server Error"}
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__boom_test__"
        ]
