"""Browser check: a long list of chats in the left rail folds to the 10 most recent.

A headless Chromium opens a Solo account with 14 chats. The rail shows the 10 newest plus a "Show 4 more" row;
the chat you are in always stays visible, "Show more" / "Show fewer" toggles the rest, and the rail search
finds a folded chat. Spec: docs/specs/chat-rail-fold-long-list.md. Skipped when Playwright is not installed.
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

TOTAL = 14


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server: a Solo admin with 14 chats, "Chat 1" the newest and "Chat 14" the oldest."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("railcap")
    saved = {
        k: os.environ.get(k)
        for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY", "ANTHILL_WIKI_ROOT")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_WIKI_ROOT"] = str(d / "wikis")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

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
    s.add(OrgSettings(org_id=o.id))
    now = dt.datetime.now(dt.timezone.utc)
    ids = []
    for i in range(1, TOTAL + 1):
        c = Conversation(
            org_id=o.id,
            user_id=u.id,
            title=f"Chat {i}",
            updated_at=now - dt.timedelta(minutes=i),
        )
        s.add(c)
        s.flush()
        ids.append(c.id)
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

    yield f"http://127.0.0.1:{port}", token, ids

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _shown(page) -> list[str]:
    return page.evaluate(
        "Array.from(document.querySelectorAll('.rail-conv-row'))"
        ".filter(r => getComputedStyle(r).display !== 'none')"
        ".map(r => r.querySelector('.rail-conv').textContent.trim())"
    )


def _open(p, base, token, conv_id):
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    page.wait_for_selector(".rail-conv-row")
    return browser, page


def test_rail_folds_to_ten_and_shows_the_rest_on_demand(live):
    base, token, ids = live
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, base, token, ids[0])  # the newest chat is open
        try:
            assert _shown(page) == [f"Chat {i}" for i in range(1, 11)]
            more = page.locator(".rail-more")
            assert more.inner_text().strip() == "Show 4 more"
            more.click()
            assert len(_shown(page)) == TOTAL
            assert page.locator(".rail-more").inner_text().strip() == "Show fewer"
            page.locator(".rail-more").click()
            assert len(_shown(page)) == 10
        finally:
            browser.close()


def test_open_chat_stays_visible_and_search_finds_folded_chats(live):
    base, token, ids = live
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, base, token, ids[12])  # Chat 13 is beyond the first ten
        try:
            shown = _shown(page)
            assert "Chat 13" in shown and len(shown) == 11
            assert page.locator(".rail-more").inner_text().strip() == "Show 3 more"
            page.fill("#railSearch", "Chat 14")
            assert _shown(page) == ["Chat 14"]
            assert page.locator(".rail-more").count() == 0
            page.fill("#railSearch", "")
            assert len(_shown(page)) == 11
        finally:
            browser.close()
