"""Visual/browser check: a headless Chromium drives the real Connectors page and verifies the
connector gallery JavaScript that unit tests can't run - clicking a tile opens the guided add
panel, a token connector renders its credential field, and a setup-required connector shows the
provider-setup notice. Runs in its own CI job; skipped when Playwright isn't installed.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server (background thread) with a seeded org/admin + a session token."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("browser")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    eng = create_engine(f"sqlite:///{d / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    # onboarding_done so the first-run welcome modal (a full-screen backdrop) doesn't sit over
    # the gallery and swallow clicks - a real admin past onboarding sees the page directly.
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True, onboarding_done=True)
    s.add(u)
    s.commit()
    token = make_token(u.id, o.id, "admin")

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app_mod.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    yield f"http://127.0.0.1:{port}", token

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_connector_gallery_guided_add(live):
    base, token = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/connectors/mcp", wait_until="domcontentloaded")
            page.wait_for_selector(".connector-grid")

            # The gallery rendered tiles for the headline connectors.
            assert page.get_by_text("Add a connector").first.is_visible()
            assert page.locator(".connector-tile", has_text="Google Drive").count() >= 1

            # A token connector (GitHub) -> guided panel opens with a credential field.
            page.locator(".connector-tile", has_text="GitHub").first.click()
            page.wait_for_selector("#guided-add", state="visible")
            assert page.locator("#ga-field-token").is_visible()  # the PAT field rendered

            # A setup-required connector (Google Drive) -> provider-setup notice shows.
            page.locator(".connector-tile", has_text="Google Drive").first.click()
            page.wait_for_selector("#guided-add", state="visible")
            assert page.locator("#ga-setup .alert-warn").is_visible()
        finally:
            browser.close()


def test_atlassian_shows_needs_setup_not_a_false_one_click(live):
    # Test-agent QA bug, 2026-10-01: Atlassian is oauth+http in the catalog (the shape every OTHER
    # one-click connector has), but its own note says one-click OAuth isn't live for it yet (SSE
    # transport, not yet wired) - guided is deliberately False to reflect that. The gallery's inline
    # message used to key off oauth+http alone, so Atlassian showed a green "One-click connect... No
    # setup needed" message directly contradicting its own amber badge and its own note underneath -
    # a three-way self-contradiction on one card. Fixed by requiring guided too; this locks it in.
    base, token = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/connectors/mcp", wait_until="domcontentloaded")
            page.wait_for_selector(".connector-grid")

            page.locator(".connector-tile", has_text="Jira and Confluence").first.click()
            page.wait_for_selector("#guided-add", state="visible")
            assert page.locator("#ga-setup .alert-warn").is_visible()  # needs-setup, not one-click
            assert page.locator("#ga-setup .alert-ok").count() == 0  # never the green claim
        finally:
            browser.close()
