"""Browser check (#94): every page that uses the navigation rail keeps its content wide below 880px.

Below the desktop app's 880px minimum width the rail becomes a top bar. Pages lay the rail out in two ways
(base.html's .layout, and the chat page's own flex row), so this walks every internal link in the rail at 390,
768 and 879 pixels and checks the page's content area is still nearly the full width and the rail is a bar, not
a column. Runs in its own CI job; skipped when Playwright is not installed.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

WIDTHS = [390, 768, 879]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org and admin."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("railnarrow")
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
    s.add(Conversation(org_id=o.id, user_id=u.id, title="A chat"))
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
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# The page's content area: the chat page has its own .chat-layout, every other page uses .main.
CONTENT_JS = """
() => {
  const sidebar = document.querySelector('.sidebar');
  const content = document.querySelector('.chat-layout') || document.querySelector('.main');
  if (!sidebar || !content) return null;
  const s = sidebar.getBoundingClientRect(), c = content.getBoundingClientRect();
  return {sidebarW: s.width, sidebarH: s.height, contentW: c.width, contentH: c.height, vw: window.innerWidth};
}
"""


def _rail_links(live, p):
    base, token = live
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    try:
        ctx = browser.new_context(service_workers="block", viewport={"width": 1200, "height": 800})
        ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
        page = ctx.new_page()
        page.goto(f"{base}/tasks", wait_until="domcontentloaded")
        hrefs = page.evaluate(
            "Array.from(document.querySelectorAll('.sidebar nav a[href^=\"/\"]')).map(a => a.getAttribute('href'))"
        )
    finally:
        browser.close()
    seen = []
    for h in hrefs:
        h = h.split("#")[0]
        if h and h not in seen and not h.startswith(("/logout", "/static", "/files")):
            seen.append(h)
    return seen


def test_every_rail_page_keeps_its_content_wide_and_the_rail_a_bar(live):
    base, token = live
    problems, visited = [], 0
    with sync_api.sync_playwright() as p:
        links = ["/chat", *_rail_links(live, p)]
        assert len(links) >= 8, links
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            for width in WIDTHS:
                ctx = browser.new_context(
                    service_workers="block", viewport={"width": width, "height": 800}
                )
                ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
                page = ctx.new_page()
                for href in dict.fromkeys(links):
                    resp = page.goto(f"{base}{href}", wait_until="domcontentloaded")
                    if resp is None or resp.status >= 400:
                        continue
                    m = page.evaluate(CONTENT_JS)
                    if m is None:  # a page without the rail
                        continue
                    visited += 1
                    if (
                        m["contentW"] < 0.9 * width
                        or m["sidebarH"] > 160
                        or m["sidebarW"] < 0.95 * width
                    ):
                        problems.append((width, href, {k: round(v) for k, v in m.items()}))
                ctx.close()
        finally:
            browser.close()
    assert visited >= 8 * len(WIDTHS), visited
    assert problems == [], problems


def test_the_chat_page_has_room_for_the_conversation_at_phone_width(live):
    base, token = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(
                service_workers="block", viewport={"width": 390, "height": 844}
            )
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/chat", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            box = page.locator(".chat-layout").bounding_box()
            assert box["width"] >= 390 * 0.95 and box["height"] >= 400, box
            composer = page.locator("#msg-input")
            assert composer.is_visible()
            b = composer.bounding_box()
            assert b["x"] >= 0 and b["x"] + b["width"] <= 390 + 0.5, b
        finally:
            browser.close()
