"""Browser checks for model settings JavaScript that unit tests cannot run: provider-specific fields
and the expired-session redirect from Refresh Models. Runs in its own CI job; the whole module is
skipped when Playwright is not installed, so the model-free test job stays green.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip(
    "playwright.sync_api"
)  # skip this module without Playwright installed


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _launch_chromium(playwright):
    # GitHub runners already provide Chrome; use it in CI to avoid flaky apt mirror access.
    return playwright.chromium.launch(channel="chrome" if os.environ.get("CI") else None)


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
    from anthill.web.db import Organization, OrgSettings, User

    eng = create_engine(f"sqlite:///{d / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    token = make_token(u.id, o.id, "admin")
    org_id = int(o.id)
    s.close()

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app_mod.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):  # wait up to ~10s for startup
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    yield f"http://127.0.0.1:{port}", token, app_mod, org_id

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _set_model_activity(app_mod, org_id, endpoint=""):
    from anthill.web.db import OrgSettings

    session = app_mod._SessionFactory()
    settings = session.query(OrgSettings).filter(OrgSettings.org_id == org_id).one()
    settings.local_model_pulling = "test-model" if endpoint == "/models/pull-status" else ""
    settings.benchmark_state = (
        json.dumps({"running": True, "candidate": "test-model"})
        if endpoint == "/models/benchmark-status"
        else ""
    )
    session.commit()
    session.close()


def test_model_tab_provider_reveals_gpu_row(live):
    base, token, _, _ = live
    with sync_api.sync_playwright() as p:
        browser = _launch_chromium(p)
        try:
            # Block the service worker: Anthill's SW auto-reloads the page on update, which would
            # reset the selection and flake the test. We're testing the page's own JS, not the SW.
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/settings/organization", wait_until="domcontentloaded")
            page.wait_for_selector("#org_provider")

            # there is no separate training-provider picker - training follows the org cloud
            assert page.locator("#training_backend").count() == 0

            # on-prem has no cloud GPU picker; a cloud provider reveals it
            page.select_option("#org_provider", "onprem")
            assert not page.locator("#gpu_row").is_visible()
            page.select_option("#org_provider", "aws")
            assert page.locator("#gpu_row").is_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("endpoint", ["/models/pull-status", "/models/benchmark-status"])
def test_expired_session_poll_redirects_to_login(live, endpoint):
    base, token, app_mod, org_id = live
    _set_model_activity(app_mod, org_id, endpoint)
    with sync_api.sync_playwright() as p:
        browser = _launch_chromium(p)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{base}/models", wait_until="domcontentloaded")

            ctx.clear_cookies()

            page.wait_for_url(f"{base}/login")
            assert errors == []
        finally:
            browser.close()
            _set_model_activity(app_mod, org_id)


# test_expired_session_refresh_redirects_to_login (a click on the old #refresh-catalog-btn) was removed
# with that button (2026-09-29 - the curated model catalog moved to Settings, /models kept only the
# custom-tag form and Installed list). It exercised the same fetchModelJSON's r.redirected handling
# that test_expired_session_poll_redirects_to_login already covers via the pull-status/benchmark-status
# polls above - a click and a poll both call that one helper, so no coverage was lost.
