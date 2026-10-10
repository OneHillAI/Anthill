"""Browser checks: the chat's Thinking button, per-chat choices, and the first-use notice
(docs/specs/chat-thinking-toggle.md).

A headless Chromium opens chats and checks that the Thinking label flips and survives a reload, that the next
message goes out with think=false, that one chat's choice does not leak into another, that a chat with no choice
follows the Settings default, and that the first-use notice appears once and saves the defaults it changes.
The stream response is faked, so no model is needed. Runs in its own CI job; skipped without Playwright.
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

    d = tmp_path_factory.mktemp("chatthink")
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
    conv2 = Conversation(org_id=o.id, user_id=u.id, title="t2")
    conv3 = Conversation(org_id=o.id, user_id=u.id, title="t3", plane="org")
    s.add_all([conv, conv2, conv3])
    s.commit()
    token = make_token(u.id, o.id, "admin")
    conv_id, conv2_id, conv3_id, user_id = conv.id, conv2.id, conv3.id, u.id

    def reset(*, thinking_on=True, web_access_on=True, seen=False):
        """Put the user's saved defaults and notice state back, for a test that needs a known start. The
        arguments default to an account that has both switched on; a new account has both off."""
        ss = app_mod._SessionFactory()
        me = ss.query(User).filter(User.id == user_id).first()
        me.thinking_on, me.web_access_on, me.chat_defaults_notice_seen = (
            thinking_on,
            web_access_on,
            seen,
        )
        ss.commit()
        ss.close()

    def saved_state():
        ss = app_mod._SessionFactory()
        me = ss.query(User).filter(User.id == user_id).first()
        out = (me.thinking_on, me.web_access_on, me.chat_defaults_notice_seen)
        ss.close()
        return out

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

    yield {
        "base": f"http://127.0.0.1:{port}",
        "token": token,
        "c1": conv_id,
        "c2": conv2_id,
        "c3": conv3_id,
        "reset": reset,
        "saved": saved_state,
    }

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _open(p, live, conv):
    ctx = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    context = ctx.new_context(service_workers="block")
    context.add_cookies([{"name": "session_token", "value": live["token"], "url": live["base"]}])
    return ctx, context


def test_a_new_account_starts_with_thinking_and_web_search_off(live):
    live["reset"](thinking_on=False, web_access_on=False, seen=False)  # what a new account gets
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            # the first-use notice is there and says both are off
            assert page.locator("#first-use-notice").is_visible()
            assert page.locator("#fu-think-state").inner_text() == "off"
            assert page.locator("#fu-web-state").inner_text() == "off"
            button = page.locator("#opt-think")
            assert button.inner_text().strip().endswith("Thinking off")
            assert button.get_attribute("aria-pressed") == "false"
            page.click("#opt-toggle")  # open Options
            assert page.locator("#opt-web").is_checked() is False

            button.click()  # one click turns Thinking on for this chat
            assert button.inner_text().strip().endswith("Thinking on")
            assert button.get_attribute("aria-pressed") == "true"

            page.locator(
                "#fu-web"
            ).check()  # switching web search on in the notice saves the default
            for _ in range(100):
                if live["saved"]()[1] is True:
                    break
                page.wait_for_timeout(50)
            assert live["saved"]() == (False, True, True)  # saved, and the notice counts as seen
        finally:
            browser.close()


def test_thinking_button_is_per_chat_and_is_sent(live):
    live["reset"](
        seen=True
    )  # an account with Thinking on (the helper's default), no notice in the way
    base, c1, c2 = live["base"], live["c1"], live["c2"]
    stream_urls: list[str] = []

    def fake_stream(route):
        stream_urls.append(route.request.url)
        route.fulfill(
            status=200,
            headers={"content-type": "text/event-stream"},
            body='data: {"token": "ok"}\n\ndata: [DONE]\n\n',
        )

    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.route("**/chat/*/stream*", fake_stream)
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")

            button = page.locator("#opt-think")
            assert button.is_visible()
            assert button.inner_text().strip().endswith("Thinking on")  # this account has it on

            button.click()
            assert button.inner_text().strip().endswith("Thinking off")

            page.reload(wait_until="domcontentloaded")  # this chat remembers its own choice
            assert page.locator("#opt-think").inner_text().strip().endswith("Thinking off")

            page.goto(
                f"{base}/chat/{c2}", wait_until="domcontentloaded"
            )  # another chat is unaffected
            assert page.locator("#opt-think").inner_text().strip().endswith("Thinking on")

            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.wait_for_function("typeof sendMessage === 'function'")  # the page script has run
            page.fill("#msg-input", "hello")
            page.click("#send-btn")
            for _ in range(100):
                if stream_urls:
                    break
                page.wait_for_timeout(
                    50
                )  # lets Playwright run the route handler (time.sleep would not)
            assert stream_urls, "the message never reached the stream endpoint"
            assert "think=false" in stream_urls[0]

            page.locator("#opt-think").click()  # back on for this chat
            assert page.locator("#opt-think").inner_text().strip().endswith("Thinking on")
        finally:
            browser.close()


def test_web_search_is_per_chat_too(live):
    live["reset"](seen=True)
    base, c1, c2 = live["base"], live["c1"], live["c2"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.click("#opt-toggle")  # open Options
            assert page.locator("#opt-web").is_checked() is True  # this account has web search on
            page.locator("#opt-web").uncheck()  # this chat turns it off
            page.goto(f"{base}/chat/{c2}", wait_until="domcontentloaded")
            page.click("#opt-toggle")
            assert (
                page.locator("#opt-web").is_checked() is True
            )  # the other chat kept the default (on)
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.click("#opt-toggle")
            assert (
                page.locator("#opt-web").is_checked() is False
            )  # this chat kept its own choice (off)
        finally:
            browser.close()


def test_first_use_notice_saves_defaults_and_chats_follow_them(live):
    live["reset"](thinking_on=True, web_access_on=True, seen=False)
    base, c1, c2 = live["base"], live["c1"], live["c2"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(
                f"{base}/tasks", wait_until="domcontentloaded"
            )  # whichever of chat/agents/tasks is first
            notice = page.locator("#first-use-notice")
            assert notice.is_visible()
            assert page.locator("#fu-think-state").inner_text() == "on"
            assert page.locator("#fu-web-state").inner_text() == "on"

            page.locator(
                "#fu-think"
            ).uncheck()  # turn thinking off as the default, right in the notice
            for _ in range(100):
                if live["saved"]()[0] is False:
                    break
                page.wait_for_timeout(50)
            assert live["saved"]() == (False, True, True)  # saved, and the notice counts as seen

            page.goto(f"{base}/chat/{c2}", wait_until="domcontentloaded")
            assert page.locator("#first-use-notice").count() == 0  # shown once
            assert (
                page.locator("#opt-think").inner_text().strip().endswith("Thinking off")
            )  # follows the default

            page.locator("#opt-think").click()  # this chat opts back in
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            assert (
                page.locator("#opt-think").inner_text().strip().endswith("Thinking off")
            )  # c1 still follows
        finally:
            browser.close()


def test_got_it_hides_the_notice_and_it_stays_hidden(live):
    live["reset"](seen=False)
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.wait_for_function("typeof fuSave === 'function'")
            page.click("#fu-ok")
            page.wait_for_selector("#first-use-notice", state="hidden")
            for _ in range(100):
                if live["saved"]()[2] is True:
                    break
                page.wait_for_timeout(50)
            assert live["saved"]()[2] is True
            page.reload(wait_until="domcontentloaded")
            assert page.locator("#first-use-notice").count() == 0
        finally:
            browser.close()


def test_opening_settings_from_the_notice_still_saves_that_it_was_seen(live):
    live["reset"](seen=False)
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            page.wait_for_function("typeof fuSave === 'function'")
            page.click("#first-use-notice a[href='/personalize#privacy']")  # navigates at once
            page.wait_for_url("**/personalize*")
            for _ in range(100):
                if live["saved"]()[2] is True:
                    break
                page.wait_for_timeout(50)
            assert live["saved"]()[2] is True  # the save survived the navigation (keepalive)
        finally:
            browser.close()


def test_an_old_browser_wide_off_becomes_the_account_default_once(live):
    live["reset"](seen=True, web_access_on=True)
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.evaluate(
                "localStorage.setItem('anthill_web_on', '0')"
            )  # the old saved choice: off
            page.reload(wait_until="domcontentloaded")
            for _ in range(100):
                if live["saved"]()[1] is False:
                    break
                page.wait_for_timeout(50)
            assert live["saved"]()[1] is False  # a user who had web off here keeps it off
            assert (
                page.evaluate("localStorage.getItem('anthill_web_on')") is None
            )  # and it never overrides again
        finally:
            browser.close()


def test_an_old_browser_wide_on_is_dropped_not_promoted(live):
    live["reset"](seen=True, web_access_on=False)  # the account has web search off
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            page.evaluate(
                "localStorage.setItem('anthill_web_on', '1')"
            )  # an old "on" from this one browser
            sent: list[str] = []
            page.on(
                "request",
                lambda r: sent.append(r.url) if "/settings/chat-defaults" in r.url else None,
            )
            page.reload(wait_until="networkidle")
            page.wait_for_function("localStorage.getItem('anthill_web_on') === null")  # removed
            assert sent == []  # nothing was sent to the server: no save of a stored "on"
            assert live["saved"]()[1] is False  # the account default is untouched
        finally:
            browser.close()


def test_importing_an_old_off_updates_the_notice_on_the_same_page(live):
    live["reset"](seen=False, web_access_on=True)
    base, c1 = live["base"], live["c1"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c1)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c1}", wait_until="domcontentloaded")
            assert page.locator("#fu-web-state").inner_text() == "on"
            page.evaluate(
                "localStorage.setItem('anthill_web_on', '0')"
            )  # the old saved choice: off
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('fu-web-state').textContent === 'off'")
            assert (
                page.locator("#fu-web").is_checked() is False
            )  # the notice no longer states the old default
        finally:
            browser.close()


def test_a_changed_web_default_never_rewrites_an_organisation_chat(live):
    # applyChatDefaults is what runs after a default is saved. Called directly, so there is no race with the
    # network: a personal chat without its own choice follows it, an organisation chat never does.
    live["reset"](seen=True, web_access_on=True)
    base, c2, c3 = live["base"], live["c2"], live["c3"]
    with sync_api.sync_playwright() as p:
        browser, context = _open(p, live, c2)
        try:
            page = context.new_page()
            page.goto(f"{base}/chat/{c2}", wait_until="domcontentloaded")
            page.click("#opt-toggle")
            page.evaluate("window.applyChatDefaults({web_access: false})")
            assert (
                page.locator("#opt-web").is_checked() is False
            )  # personal chat, no choice of its own: follows
            page.goto(f"{base}/chat/{c3}", wait_until="domcontentloaded")
            page.click("#opt-toggle")
            page.evaluate("window.applyChatDefaults({web_access: false})")
            assert page.locator("#opt-web").is_checked() is True  # organisation chats stay on
        finally:
            browser.close()
