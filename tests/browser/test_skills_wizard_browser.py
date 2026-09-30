"""Visual/browser check: a headless Chromium drives the real Skills page and exercises the
step-by-step wizard's client-side JavaScript (step navigation, prefill-driven auto-advance, the
step-6 summary) that unit tests can't run. Runs in its own CI job; the whole module is skipped when
Playwright isn't installed (so the model-free test job stays green).

Note: the Back button's visibility is checked via the INLINE style property (`el.style.visibility`,
what skwizRender() actually sets), not `getComputedStyle` - `.btn`'s global `transition: .15s` rule
makes `visibility` changes animate, so a computed-style check taken synchronously right after a click
can observe a mid-transition value and flake. The inline value reflects the JS's intended target
state immediately and deterministically, which is what this test actually cares about.
"""

from __future__ import annotations

import base64
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


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server (background thread) with a seeded org/member + a session token."""
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
    os.environ["ANTHILL_SKILLS_DIR"] = str(d / "builtin-skills")

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
    u = User(org_id=o.id, email="member@a.com", role="member", active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    token = make_token(u.id, o.id, "member")

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

    yield f"http://127.0.0.1:{port}", token

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _back_visibility(page):
    return page.evaluate("document.getElementById('skwiz-back').style.visibility")


def test_wizard_steps_gate_correctly_and_the_summary_reflects_real_values(live):
    base, token = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/skills", wait_until="domcontentloaded")

            page.click("#new-skill-btn")
            page.wait_for_selector('.skwiz-step[data-step="1"]')

            # Step 1 visible, later steps hidden, Back hidden on the first step
            assert page.locator('.skwiz-step[data-step="1"]').is_visible()
            assert not page.locator('.skwiz-step[data-step="2"]').is_visible()
            assert _back_visibility(page) == "hidden"

            # Skip the AI draft (step 1, needs a live model) and advance to step 2, where the real
            # fields live - this is exactly what a user who writes the skill by hand does.
            page.click("#skwiz-next")
            page.wait_for_selector('.skwiz-step[data-step="2"]')
            assert page.locator('.skwiz-step[data-step="2"]').is_visible()
            assert not page.locator('.skwiz-step[data-step="1"]').is_visible()
            assert _back_visibility(page) == "visible"  # no longer hidden once past step 1

            page.fill('[name="name"]', "Weekly Investor Update")
            page.fill('[name="when_to_use"]', "when asked to draft the investor update")
            page.fill('[name="description"]', "Drafts our weekly investor update")

            # Jump straight to the instructions step via the indicator, fill it, then jump to Save
            page.click('.skwiz-step-i[data-step="3"]')
            page.wait_for_selector('.skwiz-step[data-step="3"]')
            page.fill('[name="instructions"]', "Step 1: pull metrics. Step 2: draft.")
            page.click('.skwiz-step-i[data-step="6"]')
            page.wait_for_selector('.skwiz-step[data-step="6"]')

            # The save button relabels on the final step
            assert page.locator("#skwiz-next").inner_text() == "Save skill"

            # The summary - the ONLY place a full preview appears - reflects the real field values
            summary = page.locator("#skill-summary").inner_text()
            assert "Weekly Investor Update" in summary
            assert "when asked to draft the investor update" in summary

            # Save and confirm the skill round-trips through creation and back onto the list.
            with page.expect_navigation():
                page.click("#skwiz-next")
            assert page.get_by_text("Weekly Investor Update", exact=True).is_visible()
        finally:
            browser.close()
