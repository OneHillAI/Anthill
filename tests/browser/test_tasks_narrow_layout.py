"""Browser check (#94): Tasks is usable on narrow browser and PWA viewports, and unchanged on the desktop app.

A headless Chromium opens /tasks at 390x844 and 768x1024 (a phone and a tablet in the browser or an installed
PWA) and at 880x600 and 1200x820 (the Tauri app, whose minimum width is 880). It checks that nothing scrolls
sideways, that the navigation does not take a fixed column, and that creating a task, the row actions and the
queue panel can all be reached and used. Runs in its own CI job; skipped when Playwright is not installed.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time
from datetime import datetime, timezone

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

NARROW = [(390, 844), (768, 1024)]
DESKTOP = [(880, 600), (1200, 820)]

LONG_TITLE = "Weekly summary of every open action across the whole organisation and its teams"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with an admin and a few tasks, one with a long title and one with a queue."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("tasksnarrow")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, ScheduledTask, User

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
    now = datetime.now(timezone.utc)
    for title, status, extra in (
        (
            LONG_TITLE,
            "pending",
            {"queued_inputs": '["follow up on the budget"]', "cadence_needs_review": True},
        ),
        (
            "Daily digest",
            "done",
            {
                "verify_needs_review": True,
                "verify_reason": "Did not cover the goal",
                "next_run_at": None,  # not due, so the live scheduler leaves its stored result alone
                "last_result": "## Totals\n\n| item | count |\n|------|-------|\n| open | 4 |\n| closed | 9 |\n",
            },
        ),
        ("Failing task", "failed", {}),
    ):
        s.add(
            ScheduledTask(
                org_id=o.id,
                created_by=u.id,
                title=title,
                goal="Summarise the week and list the open actions for the whole team",
                schedule="daily",
                status=status,
                run_count=3,
                **{"last_run_at": now, "next_run_at": now, "last_result": "All good", **extra},
            )
        )
    s.commit()
    token = make_token(u.id, o.id, "admin")
    table_task_id = s.query(ScheduledTask).filter_by(title="Daily digest").one().id

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

    yield f"http://127.0.0.1:{port}", token, table_task_id

    server.should_exit = True
    thread.join(timeout=5)
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


OVERFLOW_JS = """
() => {
  const de = document.documentElement, main = document.querySelector('.main');
  return {page: de.scrollWidth - window.innerWidth, workspace: main ? main.scrollWidth - main.clientWidth : 0};
}
"""

INSIDE_JS = """
(el) => { const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.left >= -0.5 && r.right <= window.innerWidth + 0.5; }
"""


def _open(live, p, width, height, path="/tasks"):
    base, token = live[0], live[1]
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block", viewport={"width": width, "height": height})
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}{path}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    return browser, page, errors


def _assert_no_sideways_scroll(page, where):
    o = page.evaluate(OVERFLOW_JS)
    assert o["page"] <= 0 and o["workspace"] <= 0, (where, o)


@pytest.mark.parametrize("size", NARROW)
def test_nothing_scrolls_sideways_and_navigation_does_not_take_a_column(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, errors = _open(live, p, *size)
        try:
            _assert_no_sideways_scroll(page, "list")
            sidebar = page.locator(".sidebar").bounding_box()
            main = page.locator(".main").bounding_box()
            # The navigation is a bar across the top, not a column beside the content.
            assert sidebar["width"] >= size[0] * 0.95 and sidebar["height"] <= 120, sidebar
            assert main["width"] >= size[0] * 0.95, main
            # It opens from a menu button and closes again.
            toggle = page.locator("#nav-toggle")
            assert toggle.is_visible()
            assert not page.locator('.sidebar nav a[href="/tasks"]').is_visible()
            toggle.click()
            assert page.locator('.sidebar nav a[href="/tasks"]').is_visible()
            assert toggle.get_attribute("aria-expanded") == "true"
            toggle.click()
            assert not page.locator('.sidebar nav a[href="/tasks"]').is_visible()
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("size", NARROW)
def test_task_data_and_every_action_stay_readable_and_reachable(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size)
        try:
            rows = page.locator("table tbody tr:not([id^=q-])")
            assert rows.count() == 3
            for i in range(rows.count()):
                row = rows.nth(i)
                # Title, status, schedule and the date are shown, and nothing is cut off by the viewport.
                for sel in ("b", ".badge", "[data-utc]"):
                    el = row.locator(sel).first
                    assert el.is_visible() and el.evaluate(INSIDE_JS), (i, sel)
                for name in ("Edit", "▶ Now", "+ Queue", "✕"):
                    btn = row.get_by_role("button", name=name).first
                    assert btn.is_visible() and btn.evaluate(INSIDE_JS), (i, name)
            _assert_no_sideways_scroll(page, "rows")
        finally:
            browser.close()


@pytest.mark.parametrize("size", NARROW)
def test_the_queue_panel_opens_and_can_be_used(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size)
        try:
            page.get_by_role("button", name="+ Queue", exact=False).first.click()
            panel = page.locator("[id^=q-]:visible").first
            assert panel.is_visible()
            field = panel.locator('input[name="instruction"]')
            button = panel.get_by_role("button", name="Queue it")
            for el in (field, button):
                el.scroll_into_view_if_needed()
                assert el.is_visible() and el.evaluate(INSIDE_JS)
            assert field.bounding_box()["width"] >= 150
            _assert_no_sideways_scroll(page, "queue open")
        finally:
            browser.close()


@pytest.mark.parametrize("size", NARROW)
def test_the_create_dialog_fits_and_a_task_can_be_created(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size)
        try:
            page.get_by_role("button", name="+ New task").click()
            dialog = page.locator("#new-task")
            assert dialog.is_visible()
            card = dialog.locator(".modal-card")
            box = card.bounding_box()
            assert box["x"] >= -0.5 and box["x"] + box["width"] <= size[0] + 0.5, box
            for sel in ("#task-title", "#task-schedule", "#task-goal", "#task-submit"):
                el = dialog.locator(sel)
                el.scroll_into_view_if_needed()
                assert el.is_visible() and el.evaluate(INSIDE_JS), sel
            dialog.locator("#task-title").fill("Phone-made task")
            dialog.locator("#task-goal").fill("Check the narrow layout end to end")
            dialog.locator("#task-submit").click()
            page.wait_for_selector("text=Phone-made task")
            _assert_no_sideways_scroll(page, "after create")
        finally:
            browser.close()


@pytest.mark.parametrize("size", NARROW)
def test_the_edit_dialog_and_the_result_page_fit(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size)
        try:
            page.get_by_role("button", name="Edit").first.click()
            card = page.locator("#new-task .modal-card")
            box = card.bounding_box()
            assert box["x"] >= -0.5 and box["x"] + box["width"] <= size[0] + 0.5, box
            page.keyboard.press("Escape")
            page.get_by_role("link", name="Result").first.click()
            page.wait_for_selector("text=Run history")
            _assert_no_sideways_scroll(page, "result page")
        finally:
            browser.close()


@pytest.mark.parametrize("size", DESKTOP)
def test_the_desktop_app_keeps_its_side_rail_and_its_minimum_width(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, errors = _open(live, p, *size)
        try:
            _assert_no_sideways_scroll(page, "desktop")
            sidebar = page.locator(".sidebar").bounding_box()
            assert 150 <= sidebar["width"] <= 260 and sidebar["x"] == 0, (
                sidebar
            )  # a column beside the content
            assert sidebar["height"] >= size[1] - 1, sidebar
            assert not page.locator("#nav-toggle").is_visible()
            assert page.locator('.sidebar nav a[href="/tasks"]').is_visible()
            assert errors == []
        finally:
            browser.close()


def test_the_tauri_window_keeps_its_880_pixel_minimum():
    import json
    from pathlib import Path

    conf = json.loads((Path(__file__).parents[2] / "src-tauri" / "tauri.conf.json").read_text())
    assert conf["app"]["windows"][0]["minWidth"] == 880


@pytest.mark.parametrize("size", NARROW)
def test_a_markdown_table_in_a_result_keeps_its_header_row_and_cells(live, size):
    """The stacked-card rules are for the task list only; a result's own table must stay a table."""
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size, path=f"/tasks/{live[2]}/result")
        try:
            page.wait_for_selector("#task-result table")
            table = page.locator("#task-result table")
            assert table.evaluate("el => getComputedStyle(el).display") in ("table", "block")
            head = table.locator("thead th").first
            assert (
                head.is_visible()
                and head.evaluate("el => getComputedStyle(el).display") == "table-cell"
            )
            cell = table.locator("tbody td").first
            assert cell.evaluate("el => getComputedStyle(el).display") == "table-cell"
            _assert_no_sideways_scroll(page, "result with a table")
        finally:
            browser.close()


@pytest.mark.parametrize("size", NARROW)
def test_the_schedule_cell_reads_as_lines(live, size):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, *size)
        try:
            text = page.locator('td[data-label="Schedule"] .cell').first.inner_text()
            assert "\nNext run:" in text, text
        finally:
            browser.close()


def test_the_agents_create_dialog_keeps_its_width_below_880px(live):
    with sync_api.sync_playwright() as p:
        browser, page, _errors = _open(live, p, 768, 1024, path="/agents")
        try:
            page.evaluate("document.getElementById('new-agent').style.display = 'block'")
            width = page.locator("#new-agent .modal-card").bounding_box()["width"]
            assert width <= 560 + 0.5, width  # the shared dialog rule is unchanged outside Tasks
        finally:
            browser.close()
