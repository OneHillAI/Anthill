"""Browser check: a conversation whose stored history links a /files/ path loads without a script error.

enhancePreviews() runs while the chat script is still starting up. It used to read PREVIEWABLE before it was
declared, so the TypeError stopped the script: the file preview never appeared and the message input
(``const inp``) was never initialised, so that chat could not send after a reload. Skipped when Playwright is
not installed.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ANSWER = "Here is the file: [report](/files/report.pdf), the picture /files/pic.png and the notes /files/notes.md"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org/admin and a conversation whose answer links two files."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("chatfiles")
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
    s.add(ChatMessage(conversation_id=conv.id, role="assistant", content=ANSWER))
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
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_history_with_file_links_loads_without_a_script_error_and_shows_previews(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
            page.wait_for_selector('a[href="/files/report.pdf"]')
            page.wait_for_load_state("networkidle")

            assert errors == []  # the startup script ran to the end
            # The inline previews were attached under both links (a PDF frame, an image).
            assert page.locator('a[href="/files/report.pdf"] + div iframe').count() == 1
            assert page.locator('a[href="/files/pic.png"] + div img').count() == 1
            # A file type that is not previewable gets a link but no preview box.
            assert page.locator('a[href="/files/notes.md"]').count() == 1
            assert page.locator('a[href="/files/notes.md"] + div').count() == 0
            # The message input initialised, so this chat can send after a reload: send one and see it appear.
            assert page.evaluate("(() => { try { return !!inp; } catch (e) { return false; } })()")
            page.route(
                "**/chat/*/stream*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='data: {"token": "Got it."}\n\ndata: [DONE]\n\n',
                ),
            )
            page.fill("#msg-input", "a follow-up question")
            page.click("#send-btn")
            page.wait_for_selector("#messages .msg.user .bubble:has-text('a follow-up question')")
            page.wait_for_selector("#messages .msg.assistant .bubble:has-text('Got it.')")
            assert errors == []
        finally:
            browser.close()
