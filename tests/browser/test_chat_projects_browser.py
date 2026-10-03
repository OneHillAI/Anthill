"""Browser check: chats are grouped by project, never by a standalone folder.

A headless Chromium opens a real chat, attaches it to a project from the chat header (history stays), sees it
under that project in the rail, removes it again (it lands under Unfiled), and uses the rail search without a
script error. Spec: docs/specs/consolidate-folders-into-projects.md. Skipped when Playwright is not installed.
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
    """A real uvicorn server: a Solo admin, one project (Roadmap) and a chat with two messages."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("chatprojects")
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
    from anthill.web.db import (
        ChatMessage,
        Conversation,
        Organization,
        OrgSettings,
        Team,
        TeamMembership,
        User,
    )

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
    team = Team(org_id=o.id, name="Roadmap", slug="roadmap", owner_id=u.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=u.id, role="owner", status="active"))
    conv = Conversation(org_id=o.id, user_id=u.id, title="Quarterly planning")
    s.add(conv)
    s.flush()
    s.add(ChatMessage(conversation_id=conv.id, role="user", content="What is on the plan?"))
    s.add(ChatMessage(conversation_id=conv.id, role="assistant", content="Three items."))
    s.commit()
    token, conv_id = make_token(u.id, o.id, "admin"), conv.id

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


def test_attach_to_a_project_after_history_then_remove(live):
    base, token, conv_id = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")

            # No folder left; an obvious Project control and a visible "+ New project".
            assert page.locator("text=New folder").count() == 0
            select = page.locator("#chat-project-select")
            assert select.is_visible()
            assert page.locator("label[for=chat-project-select]").inner_text().strip() == "Project"
            assert select.locator("option").all_inner_texts() == ["No project (Unfiled)", "Roadmap"]
            assert page.locator("a.rail-newproject").is_visible()

            # The project is in the rail at once, still empty; the chat sits under Unfiled.
            assert (
                page.locator("details.rail-project summary span", has_text="Roadmap").count() == 1
            )
            assert page.locator("details.rail-project .rail-conv").count() == 0
            assert page.locator(".rail-conv-section", has_text="Unfiled").count() == 1

            # Attach the chat that already has history: it lands under the project, history intact.
            select.select_option(label="Roadmap")
            page.wait_for_url(f"{base}/chat/{conv_id}")
            page.wait_for_selector("details.rail-project .rail-conv")
            assert (
                page.locator("details.rail-project .rail-conv")
                .inner_text()
                .strip()
                .startswith("Quarterly planning")
            )
            assert page.locator("#chat-project-select").input_value() != ""
            body = page.locator("body").inner_text()
            assert "What is on the plan?" in body and "Three items." in body

            # The rail search still works with project groups (no script error, group hides on no match).
            page.fill("#railSearch", "zzz-no-such-chat")
            assert page.locator("details.rail-project.rail-hidden").count() == 1
            page.fill("#railSearch", "")
            assert page.locator("details.rail-project.rail-hidden").count() == 0

            # Remove it from the project: it is Unfiled again, not deleted.
            page.locator("#chat-project-select").select_option(label="No project (Unfiled)")
            page.wait_for_selector(".rail-conv-section:has-text('Unfiled')")
            assert page.locator("details.rail-project .rail-conv").count() == 0
            assert page.locator(".rail-conv", has_text="Quarterly planning").count() == 1
            assert "Three items." in page.locator("body").inner_text()
            assert errors == [], errors
        finally:
            browser.close()
