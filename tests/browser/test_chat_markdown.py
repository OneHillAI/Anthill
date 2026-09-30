"""Visual/browser check: chat answers render as formatted, sanitized markdown.

A headless Chromium loads a seeded conversation whose assistant message is raw markdown
(plus a <script> injection) and asserts it renders to real HTML (headings, lists, bold,
tables, links) with the script stripped. Runs in its own CI job; the whole module is
skipped when Playwright isn't installed (so the model-free suite stays green).
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

MARKDOWN = (
    "## Heading here\n\n"
    "- first item\n"
    "- **bold** item\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "See [the docs](/docs/how-it-works) and /files/report.pdf for details.\n\n"
    "<script>window.__pwned = true;</script>\n"
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org/admin + a conversation holding one markdown answer."""
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
    msg = ChatMessage(conversation_id=conv.id, role="assistant", content=MARKDOWN)
    s.add(msg)
    s.commit()
    token = make_token(u.id, o.id, "admin")
    conv_id, msg_id = conv.id, msg.id

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

    yield f"http://127.0.0.1:{port}", token, conv_id, msg_id

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_chat_renders_sanitized_markdown(live):
    base, token, conv_id, msg_id = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
            bubble = page.locator(f"#bubble-{msg_id}")
            page.wait_for_selector(f"#bubble-{msg_id} h2")  # history rendered to HTML

            # Real formatted markdown, not raw text.
            assert bubble.locator("h2").inner_text().strip() == "Heading here"
            assert bubble.locator("ul li").count() == 2
            assert bubble.locator("strong").inner_text().strip() == "bold"
            assert bubble.locator("table").count() == 1
            assert bubble.locator("td").count() == 2  # GFM table body cells
            # Links: marked handles [text](url); bare /files/... is linkified by us.
            assert bubble.locator('a[href="/docs/how-it-works"]').count() == 1
            assert bubble.locator('a[href="/files/report.pdf"]').count() == 1
            # No raw markdown leaked through.
            assert "**bold**" not in bubble.inner_text()

            # Sanitized: the injected <script> neither rendered nor executed.
            assert bubble.locator("script").count() == 0
            assert page.evaluate("window.__pwned") in (None, False)
        finally:
            browser.close()
