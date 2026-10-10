"""Browser checks: the line under the ants names the real steps of a web-search turn
(docs/specs/108-web-turn-stream.md).

The server sends `meta.stage` events (searching, reading N pages, thinking or writing) before the first word of
a web answer. The page turns them into one honest line under the ants and clears it when the words arrive. The
stream response is faked, so no model and no network are needed. Runs in its own CI job; skipped without
Playwright.
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

    d = tmp_path_factory.mktemp("chatstage")
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


# A stand-in for the browser's EventSource that the test feeds by hand, so the stream can be held open after
# the first word and the page inspected while it is still open (a fulfilled response arrives all at once).
_HAND_FED_STREAM = """
window.__streams = [];
window.EventSource = class {
  constructor(url) { this.url = url; this.readyState = 1; window.__streams.push(this); }
  close() { this.readyState = 2; }
};
window.__push = (data) => {
  const es = window.__streams[window.__streams.length - 1];
  es.onmessage({data: typeof data === 'string' ? data : JSON.stringify(data)});
};
"""


def _page(p, base, token, conv_id, init_script=None):
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    if init_script:
        ctx.add_init_script(init_script)
    page = ctx.new_page()
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    page.wait_for_function("typeof stageText === 'function' && typeof sendMessage === 'function'")
    return browser, page


def test_the_stage_line_names_the_real_steps(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:

            def text(stage, count=0):
                return page.evaluate("m => stageText(m)", {"stage": stage, "count": count})

            assert text("searching") == "Searching the web…"
            assert text("reading", 3) == "Reading 3 pages…"
            assert text("reading", 1) == "Reading 1 page…"
            assert text("reading", 0) == "No web results. Writing the answer…"
            assert text("thinking") == "Thinking, then writing the answer…"
            assert text("writing") == "Writing the answer…"
            assert text("something-new") == ""  # an unknown step shows nothing rather than a guess
        finally:
            browser.close()


def test_the_stage_line_follows_the_steps_and_is_gone_once_the_words_arrive(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id, _HAND_FED_STREAM)
        try:
            status = page.locator("#build-status")
            page.fill("#msg-input", "population of Reykjavik?")
            page.click("#send-btn")
            page.wait_for_function("window.__streams.length === 1")

            page.evaluate("window.__push({meta: {stage: 'searching', count: 0}})")
            assert status.inner_text().strip() == "Searching the web…"
            page.evaluate("window.__push({meta: {stage: 'reading', count: 3}})")
            assert status.inner_text().strip() == "Reading 3 pages…"
            page.evaluate("window.__push({meta: {stage: 'writing', count: 0}})")
            assert status.inner_text().strip() == "Writing the answer…"

            # the first word arrives and the stream stays OPEN: the line must go now, not at the end
            page.evaluate("window.__push({token: 'Reykjavik has about '})")
            page.wait_for_selector(".msg.assistant:has-text('Reykjavik has about')")
            assert page.evaluate("window.__streams[0].readyState") == 1  # still open
            assert status.inner_text().strip() == ""

            page.evaluate("window.__push({token: '140,000 people.'})")
            page.evaluate("window.__push('[DONE]')")
            page.wait_for_selector(".msg.assistant:has-text('140,000 people.')")
            assert status.inner_text().strip() == ""
        finally:
            browser.close()


def test_the_page_says_how_many_web_results_were_left_out(live):
    base, token, conv_id = live
    body = (
        'data: {"meta": {"stage": "searching", "count": 0}}\n\n'
        'data: {"meta": {"stage": "reading", "count": 4}}\n\n'
        'data: {"meta": {"stage": "left_out", "count": 2}}\n\n'
        'data: {"meta": {"stage": "writing", "count": 0}}\n\n'
        'data: {"token": "Here is what the other pages say."}\n\n'
        "data: [DONE]\n\n"
    )
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.route(
                "**/chat/*/stream*",
                lambda route: route.fulfill(
                    status=200, headers={"content-type": "text/event-stream"}, body=body
                ),
            )
            page.fill("#msg-input", "latest news?")
            page.click("#send-btn")
            page.wait_for_selector(".msg.assistant:has-text('Here is what the other pages say.')")
            page.wait_for_selector(".msg.assistant .meta:has-text('2 web results left out')")
            assert page.locator("#build-status").inner_text().strip() == ""
        finally:
            browser.close()
