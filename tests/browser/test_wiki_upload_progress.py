"""Visual/browser check: the backend reads an uploaded file and runs it through the local AI, which is
genuinely slow for anything non-trivial, so the page must show what is going on meanwhile (founder
report, repeated; #148). The submit handler sends the form with an XHR, shows the browser's own upload
progress, then an elapsed timer against the per-step limit, and ends in a visible state if the
connection drops. Exercised directly with a stubbed XHR rather than racing a real submission against
page navigation, which would be flaky.
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


def test_upload_submit_shows_in_progress_feedback(live):
    base, token = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/wiki", wait_until="domcontentloaded")
            page.wait_for_selector('input[type="file"][name="file"]')

            # Before: the button reads "Upload" and the status bar is hidden.
            btn = page.locator('form[action="/wiki/upload"] button[type="submit"]')
            assert btn.inner_text() == "Upload"
            assert page.locator("#wiki-upload-progress").is_hidden()

            # wikiUploadSubmit does nothing (returns true) when no file is selected - exactly the
            # guard that keeps it from firing on an empty-file submit attempt.
            page.evaluate(
                "wikiUploadSubmit(document.querySelector('form[action=\"/wiki/upload\"]'))"
            )
            assert btn.inner_text() == "Upload"
            assert page.locator("#wiki-upload-progress").is_hidden()

            # Attach a real file via the File API (native file dialogs aren't scriptable). The XHR is
            # stubbed so nothing is sent (the server would try to start a model), and kept so the test
            # can fire the browser events the status bar reacts to.
            page.evaluate(
                """
                () => {
                    window.__xhr = null;
                    XMLHttpRequest.prototype.send = function () { window.__xhr = this; };
                    const input = document.querySelector('input[type="file"][name="file"]');
                    const file = new File(['hello'], 'test.md', { type: 'text/markdown' });
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    input.files = dt.files;
                }
                """
            )
            submitted = page.evaluate(
                "wikiUploadSubmit(document.querySelector('form[action=\"/wiki/upload\"]'))"
            )
            assert submitted is False  # the page stays put and shows progress instead of navigating
            status = page.locator("#wiki-upload-progress")
            assert btn.is_disabled()
            assert status.is_visible()
            assert "Uploading" in status.inner_text()

            # The server has the whole file: the elapsed timer and the per-step limit appear.
            page.evaluate("window.__xhr.upload.onload()")
            text = status.inner_text()
            assert "reading the document and writing the page" in text
            assert "s so far" in text and "60 s" in text
            assert "up to a minute" not in text

            # A dropped connection ends in a visible final state and frees the button.
            page.evaluate("window.__xhr.onerror()")
            assert "did not reach Anthill" in status.inner_text()
            assert btn.is_enabled() and btn.inner_text() == "Upload"
        finally:
            browser.close()
