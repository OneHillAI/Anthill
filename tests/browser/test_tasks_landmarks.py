"""Browser check (#90, #91, #95): the Tasks pages have one main landmark, every control has a name, repeated
controls say which task they belong to, one clear way to create a task leads the page, and the administrator
settings sit apart. Uses Playwright roles and names, the way assistive technology reads the page.
Runs in its own CI job; skipped when Playwright is not installed.
"""

from __future__ import annotations

import base64
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

# The accessible name of a control, computed from what a screen reader uses: aria-label, aria-labelledby, an
# associated label, or (for buttons and links) its own text. A placeholder or a title is never a name.
NAMES_JS = r"""
() => {
  const text = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const name = (el) => {
    const al = el.getAttribute('aria-label'); if (al && al.trim()) return al.trim();
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(text).join(' ').trim(); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && text(l)) return text(l); }
    const wrap = el.closest('label'); if (wrap && text(wrap)) return text(wrap);
    if (['BUTTON', 'A', 'SUMMARY'].includes(el.tagName)) return text(el);
    return '';
  };
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const out = [];
  document.querySelectorAll('input:not([type=hidden]), select, textarea, button, summary').forEach((el) => {
    if (el.closest('dialog') || !visible(el) || el.closest('.sidebar, .topbar, #help-panel, #help-fab, .fu-notice')) return;
    if (!name(el)) out.push(el.tagName.toLowerCase() + (el.name ? '[name=' + el.name + ']' : '') + (el.id ? '#' + el.id : ''));
  });
  return out;
}
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with an admin, a member, tasks in several states (and 45 more for paging) and a
    task whose result needs review."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("taskslandmarks")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, ScheduledTask, TaskRun, User

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
    admin = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    member = User(org_id=o.id, email="m@a.com", role="member", active=True)
    s.add_all([admin, member])
    s.flush()
    now = datetime.now(timezone.utc)

    def task(title, **kw):
        base = {
            "org_id": o.id,
            "created_by": admin.id,
            "title": title,
            "goal": "Summarise the week",
            "schedule": "daily",
            "status": "pending",
            "last_result": "All good",
            "next_run_at": None,
        }
        base.update(kw)
        t = ScheduledTask(**base)
        s.add(t)
        s.flush()
        return t

    # The list is newest first, 40 per page: make the 45 filler tasks the oldest so the named ones are on page 1.
    for i in range(45):
        task(f"Bulk {i:02d}", created_at=now - timedelta(days=2))
    ids = {
        "active": task("T active").id,
        "cancelled": task("T cancelled", status="cancelled").id,
        "done": task("T done once", status="done", schedule="once").id,
        "org": task("T shared", plane="org").id,
    }
    flagged = task("T flagged", verify_needs_review=True, verify_reason="Did not cover the goal")
    ids["flagged"] = flagged.id
    for trigger in ("manual", "scheduled"):
        s.add(
            TaskRun(
                org_id=o.id,
                task_id=flagged.id,
                trigger=trigger,
                status="ok",
                result="a result",
                finished_at=now,
                verify_needs_review=True,
                verify_reason="Did not cover the goal",
                verify_confidence="low",
            )
        )
    s.commit()
    tokens = {
        "admin": make_token(admin.id, o.id, "admin"),
        "member": make_token(member.id, o.id, "member"),
    }

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

    yield f"http://127.0.0.1:{port}", tokens, ids

    server.should_exit = True
    thread.join(timeout=5)
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _open(live, p, path="/tasks", who="admin", width=1200, height=900):
    base, tokens, _ = live
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block", viewport={"width": width, "height": height})
    ctx.add_cookies([{"name": "session_token", "value": tokens[who], "url": base}])
    page = ctx.new_page()
    page.goto(f"{base}{path}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    return browser, page


# ── #90: landmarks and names ────────────────────────────────────────────────────────────────────


def test_each_page_has_exactly_one_main_landmark(live):
    _, _, ids = live
    with sync_api.sync_playwright() as p:
        for path in ("/tasks", f"/tasks/{ids['active']}/result", "/agents", "/settings"):
            browser, page = _open(live, p, path)
            try:
                if page.locator(".sidebar").count():  # the pages that use the app layout
                    assert page.get_by_role("main").count() == 1, path
            finally:
                browser.close()


def test_the_task_table_and_the_pager_have_names(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            assert page.get_by_role("table", name="Your tasks").count() == 1
            assert page.get_by_role("navigation", name="Task pages").count() == 1
        finally:
            browser.close()


def test_every_control_outside_the_dialog_has_a_name_that_is_not_a_placeholder(live):
    _, _, ids = live
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            page.get_by_text("Task settings for administrators").click()  # open the disclosure
            page.get_by_role("button", name=re.compile("^Add follow-up to: T active")).click()
            assert page.evaluate(NAMES_JS) == []
        finally:
            browser.close()
        browser, page = _open(live, p, f"/tasks/{ids['flagged']}/result")
        try:
            assert page.evaluate(NAMES_JS) == []
        finally:
            browser.close()


def test_repeated_row_controls_say_which_task_they_belong_to(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            for name in (
                "Edit task: T active",
                "Run now: T active",
                "Add follow-up to: T active",
                "Cancel future runs: T active",
                "View result: T active",
                "Reactivate task: T cancelled",
                "Run again: T done once",
            ):
                assert (
                    page.get_by_role("button", name=name).count()
                    + page.get_by_role("link", name=name).count()
                    == 1
                ), name
            # a cancelled task offers none of the other row actions
            assert page.get_by_role("button", name="Run now: T cancelled").count() == 0
            assert page.get_by_role("button", name="Cancel future runs: T cancelled").count() == 0
        finally:
            browser.close()


def test_the_follow_up_panel_opens_with_a_labelled_field_and_reports_its_state(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            toggle = page.get_by_role("button", name="Add follow-up to: T active")
            assert toggle.get_attribute("aria-expanded") == "false"
            field = page.get_by_role("textbox", name="Follow-up instruction for T active")
            assert not field.is_visible()
            toggle.click()
            assert toggle.get_attribute("aria-expanded") == "true" and field.is_visible()
            assert page.get_by_role("button", name="Save follow-up for: T active").is_visible()
            toggle.click()
            assert toggle.get_attribute("aria-expanded") == "false" and not field.is_visible()
        finally:
            browser.close()


def test_each_mark_reviewed_button_on_the_result_page_names_its_own_run(live):
    _, _, ids = live
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p, f"/tasks/{ids['flagged']}/result")
        try:
            buttons = page.get_by_role("button", name=re.compile(r"^Mark reviewed: "))
            names = [buttons.nth(i).get_attribute("aria-label") for i in range(buttons.count())]
            assert len(names) >= 2 and len(set(names)) == len(names), (
                names
            )  # one per run, all different
            assert any("run on" in n for n in names)
        finally:
            browser.close()


# ── #91: cancelling says what it does ───────────────────────────────────────────────────────────


def test_cancel_asks_first_and_says_it_stops_future_runs_and_keeps_the_history(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            page.get_by_role("button", name="Cancel future runs: T active").click()
            dialog_text = page.locator("body").inner_text()
            assert "history is kept" in dialog_text and "nothing is deleted" in dialog_text
            assert "Anything it already did stays" in dialog_text
            assert page.get_by_role("button", name="Keep task", exact=True).count() == 1
            assert (
                page.get_by_role("button", name="Cancel future runs", exact=True).count() >= 1
            )  # the confirm
        finally:
            browser.close()


def test_a_read_only_member_sees_the_shared_task_without_controls(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p, who="member")
        try:
            row = page.get_by_role("row").filter(has_text="T shared")
            assert row.get_by_role("link", name="View result: T shared").count() == 1
            for verb in ("Edit task", "Run now", "Add follow-up", "Cancel future runs"):
                assert row.get_by_role("button", name=re.compile(f"^{verb}")).count() == 0, verb
        finally:
            browser.close()


# ── #95: one clear way to create a task, and the administrator settings apart ───────────────────


def test_one_primary_action_leads_the_page_and_the_describe_option_follows_it(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            order = page.evaluate(
                """() => {
                  const pos = (el) => el ? Array.from(document.querySelectorAll('*')).indexOf(el) : -1;
                  const q = (s) => document.querySelector(s);
                  const heading = Array.from(document.querySelectorAll('h3')).find((h) => h.textContent.includes('describe it'));
                  return {
                    newTask: pos(q('#new-task-btn')), describe: pos(heading), table: pos(q('table.tasks-list')),
                    admin: pos(q('#task-admin-settings')),
                    primaries: Array.from(document.querySelectorAll('.tasks-page button.btn-primary, .tasks-page a.btn-primary'))
                      .filter((b) => !b.closest('dialog, table, .fu-notice')).length,
                  };
                }"""
            )
            assert 0 < order["newTask"] < order["describe"] < order["table"] < order["admin"]
            assert (
                order["primaries"] == 1
            )  # the drafter and every other option are not competing primaries
            assert page.get_by_role("button", name="New task").is_visible()
            assert page.get_by_role("button", name="Draft from description").is_visible()
            assert page.get_by_role("heading", name="Or describe it in words").is_visible()
        finally:
            browser.close()


def test_the_administrator_settings_are_a_closed_disclosure_in_plain_words(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            disclosure = page.locator("#task-admin-settings")
            assert disclosure.get_attribute("open") is None
            field = page.get_by_label("Most steps a task may take in one run")
            assert not field.is_visible()
            page.get_by_text("Task settings for administrators").click()
            assert field.is_visible()
            assert page.get_by_label("Retries on your cloud model per month").is_visible()
        finally:
            browser.close()
        browser, page = _open(live, p, who="member")
        try:
            assert page.locator("#task-admin-settings").count() == 0
        finally:
            browser.close()


def test_the_page_uses_plain_words_and_explains_scope(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            page.get_by_text("Task settings for administrators").click()
            text = page.locator("main").inner_text()
            for stale in (
                "Create one below",
                "tool-loop",
                "connected backend",
                "#278",
                "Task defaults",
            ):
                assert stale not in text, stale
            assert "Only you" in text  # what Solo means, next to the word
            assert "Everyone in your organisation" in text
        finally:
            browser.close()
