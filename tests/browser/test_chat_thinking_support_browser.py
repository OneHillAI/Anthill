"""Browser checks: the Thinking button says so when the model of the chat has no thinking mode
(docs/specs/chat-thinking-toggle.md).

The page asks ``/chat/<id>/thinking-support`` after it has loaded; here the answer is faked, so no model and no
engine are needed. Runs in its own CI job; skipped without Playwright.
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
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("thinksupport")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    eng = create_engine(f"sqlite:///{d / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.flush()
    conv = Conversation(org_id=o.id, user_id=u.id, title="t")
    s.add(conv)
    s.commit()
    token = make_token(u.id, o.id, "admin")
    conv_id = conv.id

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

    yield f"http://127.0.0.1:{port}", token, conv_id

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _open(p, base, token, conv_id, answer):
    """Open the chat with the server's thinking-support answer faked; ``answer`` is the JSON body."""
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    asked: list[str] = []

    def fulfil(route):
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=answer)

    page.route("**/thinking-support", fulfil)
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    page.wait_for_function("typeof paintThinking === 'function'")
    return browser, page, asked


def test_a_model_without_a_thinking_mode_says_so_and_the_button_does_nothing(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser, page, asked = _open(
            p, base, token, conv_id, '{"known": true, "thinks": false, "model": "gemma3:4b"}'
        )
        try:
            button = page.locator("#opt-think")
            page.wait_for_function(
                "document.getElementById('opt-think-label').textContent.includes('No thinking mode')"
            )
            assert asked and asked[0].endswith(f"/chat/{conv_id}/thinking-support")
            assert button.inner_text().strip().endswith("No thinking mode")
            assert button.get_attribute("aria-disabled") == "true"
            assert (
                button.get_attribute("aria-pressed") is None
            )  # not a switch while it does nothing
            assert "gemma3:4b" in button.get_attribute("title")
            assert "makes no difference" in button.get_attribute("title")

            before = page.evaluate("[window._thinkOn, JSON.stringify(localStorage)]")
            button.click(
                force=True
            )  # Playwright treats aria-disabled as disabled; a person can still click
            after = page.evaluate("[window._thinkOn, JSON.stringify(localStorage)]")
            assert after == before  # no choice was stored for a model that cannot use it
            assert button.inner_text().strip().endswith("No thinking mode")
        finally:
            browser.close()


def test_a_model_that_thinks_keeps_the_working_button(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser, page, asked = _open(
            p, base, token, conv_id, '{"known": true, "thinks": true, "model": "qwen3.5:9b"}'
        )
        try:
            button = page.locator("#opt-think")
            page.wait_for_function("window._thinkSupport !== null")
            assert asked
            assert button.inner_text().strip().endswith("Thinking off")  # a new account's default
            assert button.get_attribute("aria-disabled") is None
            assert button.get_attribute("aria-pressed") == "false"
            button.click()
            assert button.inner_text().strip().endswith("Thinking on")
            assert button.get_attribute("aria-pressed") == "true"
            button.click()
            assert button.inner_text().strip().endswith("Thinking off")
        finally:
            browser.close()


def test_when_the_server_cannot_tell_the_button_is_left_as_it_was(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser, page, asked = _open(p, base, token, conv_id, '{"known": false}')
        try:
            button = page.locator("#opt-think")
            page.wait_for_function("true")
            for _ in range(40):  # the answer has arrived once the page has asked
                if asked:
                    break
                page.wait_for_timeout(50)
            assert asked
            page.wait_for_timeout(200)
            assert page.evaluate("window._thinkSupport") is None
            assert button.inner_text().strip().endswith("Thinking off")
            assert button.get_attribute("aria-disabled") is None
            button.click()
            assert button.inner_text().strip().endswith("Thinking on")
        finally:
            browser.close()
