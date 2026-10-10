"""Browser checks for chat proposals and task UI:

  1. The per-answer Export button reveals its format choices on click rather than showing them
     inline, and Redo-with-provider only renders when an escalation provider is attached.
  2. The intent "proposal" turn renders: a `do` proposal names the inferred format and offers
     Do it / Edit / Cancel; Edit drops the request back into the composer.
  3. A `schedule` proposal's "Do it" creates a task end-to-end via POST /chat/schedule (no model
     needed), and the card confirms it.
  4. The task create/edit dialog contains focus, owns asynchronous draft results, restores its
     opener, and isolates background UI in the dependency-free fallback.

Runs in its own CI job; the whole module is skipped when Playwright isn't installed.
"""

from __future__ import annotations

import base64
import os
import re
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ANSWER = "## Refund policy\n\nWe offer a **30 day** refund window.\n"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org/admin + a conversation holding one answer."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("chatprop")
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
    s.add(ChatMessage(conversation_id=conv.id, role="user", content="What is the refund policy?"))
    asst = ChatMessage(conversation_id=conv.id, role="assistant", content=ANSWER)
    s.add(asst)
    s.commit()
    token = make_token(u.id, o.id, "admin")
    conv_id, asst_id = conv.id, asst.id

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

    yield f"http://127.0.0.1:{port}", token, conv_id, asst_id

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _page(p, base, token, conv_id, timezone_id=None):
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    context_options = {"service_workers": "block"}
    if timezone_id:
        context_options["timezone_id"] = timezone_id
    ctx = browser.new_context(**context_options)
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    return browser, page


def test_export_reveals_formats_on_click_and_redo_needs_a_provider(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector(".msg.assistant .meta")
            meta_el = page.locator(".msg.assistant .meta").first
            meta = meta_el.inner_text()
            # The per-format choices (PDF/Word/...) aren't shown until Export is clicked...
            assert "PDF" not in meta and "Word" not in meta
            # ...and Redo only renders when an escalation provider is attached - this fixture's org
            # has none configured.
            assert "agent" not in meta
            assert meta_el.locator("button[title^='Redo with']").count() == 0
            assert meta_el.locator("button[title='Export as a document']").count() == 1
            assert (
                page.locator(".msg.assistant .meta button").count() >= 4
            )  # up/down/snippet/save/export

            meta_el.locator("button[title='Export as a document']").click()
            page.wait_for_selector(".msg.assistant .meta button:has-text('PDF')")
        finally:
            browser.close()


def test_do_proposal_renders_and_edit_fills_composer(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.evaluate(
                """() => {
                    const b = appendMsg('assistant', '');
                    b.id = 'test-do';
                    renderProposal(b, {kind:'do', format:'xlsx',
                        summary:'a customer spreadsheet',
                        message:'make a spreadsheet of customers'});
                }"""
            )
            card = page.locator("#test-do")
            card.wait_for()
            txt = card.inner_text()
            assert "Excel" in txt  # xlsx -> a human label, named for the user
            for label in ("Do it", "Edit", "Cancel"):
                assert label in txt
            # Edit drops the original request back into the composer (correct it in words).
            card.locator(".btn-edit").click()
            assert page.locator("#msg-input").input_value() == "make a spreadsheet of customers"
        finally:
            browser.close()


def test_schedule_proposal_doit_creates_task(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.evaluate(
                """() => {
                    const b = appendMsg('assistant', '');
                    b.id = 'test-sched';
                    renderProposal(b, {kind:'schedule', title:'Inbox summary',
                        schedule:'daily', goal:'Summarize my inbox each morning'});
                }"""
            )
            page.locator("#test-sched .btn-doit").click()
            # POST /chat/schedule creates a ScheduledTask (no model needed); the card confirms it.
            page.wait_for_selector("#test-sched:has-text('Scheduled')")
            timezone_name = page.evaluate("browserTimezone()")
            confirmation = page.locator("#test-sched").inner_text()
            assert "daily" in confirmation and timezone_name in confirmation

            # the task really exists in the DB
            import anthill.web.app as app_mod
            from anthill.web.db import ScheduledTask

            s = app_mod._SessionFactory()
            tasks = s.query(ScheduledTask).all()
            assert any(
                t.title == "Inbox summary" and t.schedule == "daily" and t.timezone == timezone_name
                for t in tasks
            )

            page.evaluate(
                """() => {
                    const b = appendMsg('assistant', '');
                    b.id = 'test-hourly-sched';
                    renderProposal(b, {kind:'schedule', title:'Hourly summary',
                        schedule:'hourly', goal:'Summarize my inbox every hour'});
                }"""
            )
            hourly = page.locator("#test-hourly-sched")
            assert timezone_name not in hourly.inner_text()
            hourly.locator(".btn-doit").click()
            page.wait_for_selector("#test-hourly-sched:has-text('Scheduled')")
            assert timezone_name not in hourly.inner_text()
        finally:
            browser.close()


def test_save_as_task_on_answer_creates_linked_task(live):
    """P3: 'save as task' on a past answer opens a cadence picker; scheduling creates a
    ScheduledTask linked to that message, with the originating prompt as the goal."""
    base, token, conv_id, asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector(".msg.assistant .meta")
            page.locator(
                ".msg.assistant .meta button[title='Save this as a recurring task']"
            ).first.click()
            card = page.locator(".sched-card").first
            card.wait_for()
            timezone_name = page.evaluate("browserTimezone()")
            assert timezone_name in card.inner_text()
            card.locator("select.sched-when").select_option("hourly")
            assert timezone_name not in card.inner_text()
            card.locator("select.sched-when").select_option("once")
            assert timezone_name not in card.inner_text()
            card.locator("select.sched-when").select_option("weekly")
            assert timezone_name in card.inner_text()
            card.locator(".sched-go").click()
            page.wait_for_selector(".sched-card:has-text('Scheduled')")

            import anthill.web.app as app_mod
            from anthill.web.db import Conversation, ScheduledTask

            s = app_mod._SessionFactory()
            conversation = s.get(Conversation, conv_id)
            s.add(
                ScheduledTask(
                    org_id=conversation.org_id,
                    created_by=conversation.user_id,
                    title="Another weekly task",
                    goal="unrelated",
                    schedule="weekly",
                    status="pending",
                )
            )
            s.commit()
            t = s.query(ScheduledTask).filter(ScheduledTask.source_message_id == asst_id).one()
            assert t.schedule == "weekly"
            assert "refund policy" in t.goal  # goal is the user's original prompt
            assert t.timezone == page.evaluate("browserTimezone()")
        finally:
            browser.close()


def test_live_answer_gets_real_controls_not_a_reload_placeholder(live):
    """A just-streamed answer already has a real, persisted message id (chat_stream's
    meta.message_id, sent well before [DONE]) - addAssistantControls must use it to give the SAME
    rate/snippet/save-as-task controls a reloaded history answer gets, not the old inert "reload to
    rate / save" text. Drives addAssistantControls directly (no model backend needed) to reproduce
    exactly what runStream's _finalize does, then proves the controls are real by completing a full
    thumbs-up round trip against the live server."""
    base, token, conv_id, asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector("#messages")
            page.evaluate(
                """(msgId) => {
                    const bubble = appendMsg('assistant', 'A fresh live answer.');
                    bubble.closest('.msg').setAttribute('data-id', String(msgId));
                    addAssistantControls(bubble.parentElement, 'a live question', 'A fresh live answer.', msgId);
                }""",
                asst_id,
            )
            live_msg = page.locator(f'.msg[data-id="{asst_id}"]').last
            assert "reload to rate" not in live_msg.inner_text().lower()
            up = live_msg.locator("button.thumbs").nth(0)
            down = live_msg.locator("button.thumbs").nth(1)
            snip = live_msg.locator("button.thumbs").nth(2)
            task = live_msg.locator("button.thumbs[title='Save this as a recurring task']")
            assert task.count() == 1

            up.click()
            page.wait_for_selector(f'.msg[data-id="{asst_id}"] button.selected-up')
            assert down.evaluate("el => el.classList.contains('selected-down')") is False

            import anthill.web.app as app_mod
            from anthill.web.db import ChatMessage

            s = app_mod._SessionFactory()
            assert s.get(ChatMessage, asst_id).thumbs_up is True

            snip.click()
            page.wait_for_selector("#snip-modal[style*='flex']")
            assert "fresh live answer" in page.locator("#snip-preview").inner_text()
        finally:
            browser.close()


def test_live_answer_without_a_real_id_only_offers_save_as_task(live):
    """The rare turn that finishes with no persisted-id meta ever sent (e.g. the "remember this: ..."
    quick-ack path, or a plane-unavailable error) can't rate or snippet anything - it must fall back
    to just "save as task", not silently keep the retired reload placeholder either."""
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector("#messages")
            page.evaluate(
                """() => {
                    const bubble = appendMsg('assistant', 'Saved that to memory.');
                    addAssistantControls(bubble.parentElement, 'remember this: x', 'Saved that to memory.', null);
                }"""
            )
            newest = page.locator(".msg.assistant .meta").last
            assert "reload to rate" not in newest.inner_text().lower()
            assert newest.locator("button[title='Save this as a recurring task']").count() == 1
            assert (
                newest.locator("button").count() == 1
            )  # only save-as-task, no rate/snippet/export/redo buttons
        finally:
            browser.close()


def test_task_creation_modal_keyboard_and_screen_reader(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            trigger = page.get_by_role("button", name="New task")
            trigger.click()

            dialog = page.get_by_role("dialog", name="Create a task")
            assert dialog.is_visible()
            title = dialog.get_by_label("Task title", exact=True)
            assert title.evaluate("el => el === document.activeElement")

            page.keyboard.press("Shift+Tab")
            close = dialog.get_by_role("button", name="Close task dialog")
            assert close.evaluate("el => el === document.activeElement")
            page.keyboard.press("Shift+Tab")
            cancel = dialog.get_by_role("button", name="Cancel", exact=True)
            assert cancel.evaluate("el => el === document.activeElement")
            page.keyboard.press("Tab")
            assert close.evaluate("el => el === document.activeElement")

            page.keyboard.press("Escape")
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")

            trigger.click()
            close.click()
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")

            example = page.get_by_role("button", name="Use this").first
            example.click()
            assert dialog.is_visible()
            assert title.input_value() == "📧 Daily email digest"
            page.keyboard.press("Escape")
            assert example.evaluate("el => el === document.activeElement")
        finally:
            browser.close()


def test_task_creation_modal_discards_stale_draft_edit(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            trigger = page.get_by_role("button", name="New task")
            trigger.click()
            dialog = page.get_by_role("dialog", name="Create a task")
            form = dialog.locator("#task-form")
            form.get_by_label("Task title", exact=True).fill("Draft race task")
            form.get_by_label("Goal / instruction for the agent").fill("Original goal")
            form.get_by_role("button", name="Create task").click()

            row = page.locator("tr", has_text="Draft race task")
            row.wait_for()
            page.evaluate(
                """() => {
                    const nativeFetch = window.fetch.bind(window);
                    let resolveDraft;
                    const draftResponse = new Promise(resolve => { resolveDraft = resolve; });
                    window.resolveDraft = () => resolveDraft({
                        ok: true,
                        json: () => Promise.resolve({
                            title: 'Stale draft', goal: 'Stale goal', schedule: 'daily'
                        })
                    });
                    window.fetch = (...args) => args[0] === '/tasks/draft'
                        ? draftResponse : nativeFetch(...args);
                }"""
            )
            page.locator("#task-desc").fill("Draft a daily summary")
            page.get_by_role("button", name="Draft from description").click()

            edit = row.get_by_role("button", name=re.compile(r"^Edit task: "))
            edit.click()
            dialog = page.get_by_role("dialog", name="Edit task")
            form = dialog.locator("#task-form")
            title = form.get_by_label("Task title", exact=True)
            goal = form.get_by_label("Goal / instruction for the agent")
            title.fill("Edited while drafting")
            goal.fill("Keep this edit")
            goal.focus()

            page.evaluate("window.resolveDraft()")
            page.wait_for_function("!document.getElementById('draft-btn').disabled")
            assert dialog.is_visible()
            assert form.get_attribute("action").endswith("/edit")
            assert title.input_value() == "Edited while drafting"
            assert goal.input_value() == "Keep this edit"
            assert form.get_by_role("button", name="Save changes").is_visible()
            assert goal.evaluate("el => el === document.activeElement")
            page.keyboard.press("Escape")
            assert edit.evaluate("el => el === document.activeElement")

            page.evaluate(
                """() => {
                    const previousFetch = window.fetch.bind(window);
                    let resolveFailedDraft;
                    const failedDraft = new Promise(resolve => { resolveFailedDraft = resolve; });
                    window.resolveFailedDraft = () => resolveFailedDraft({ok: false});
                    window.fetch = (...args) => args[0] === '/tasks/draft'
                        ? failedDraft : previousFetch(...args);
                }"""
            )
            page.locator("#task-desc").fill("Draft another daily summary")
            page.get_by_role("button", name="Draft from description").click()
            trigger.click()
            page.evaluate("window.resolveFailedDraft()")
            page.wait_for_function("!document.getElementById('draft-btn').disabled")
            assert page.locator("#toast").is_hidden()
            assert page.get_by_role("dialog", name="Create a task").is_visible()
            page.keyboard.press("Escape")
            assert trigger.evaluate("el => el === document.activeElement")
        finally:
            browser.close()


def test_task_creation_modal_ignores_stale_native_close(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            page.locator("#task-desc").fill("Draft a daily summary")
            page.evaluate(
                """() => {
                    const nativeFetch = window.fetch.bind(window);
                    let resolveDraft;
                    const draftResponse = new Promise(resolve => { resolveDraft = resolve; });
                    window.resolveDraft = () => resolveDraft({
                        ok: true,
                        json: () => Promise.resolve({
                            title: 'Draft title', goal: 'Draft goal', schedule: 'daily'
                        })
                    });
                    window.fetch = (...args) => args[0] === '/tasks/draft'
                        ? draftResponse : nativeFetch(...args);
                    window.taskCloseEvents = 0;
                    taskModal.addEventListener('close', () => { window.taskCloseEvents += 1; });
                }"""
            )
            trigger = page.get_by_role("button", name="New task")
            trigger.click()
            page.evaluate("() => { void draftTask(); }")
            page.evaluate("() => { closeTaskModal(); window.resolveDraft(); }")
            page.wait_for_function(
                "taskModal.open && taskModal.querySelector('#task-title').value === 'Draft title'"
                " && window.taskCloseEvents === 1"
            )

            dialog = page.get_by_role("dialog", name="Create a task")
            assert dialog.is_visible()
            page.keyboard.press("Escape")
            page.wait_for_function("window.taskCloseEvents === 2")
            draft = page.get_by_role("button", name="Draft from description")
            assert draft.evaluate("el => el === document.activeElement")
        finally:
            browser.close()


def test_task_creation_modal_fallback(live):
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.add_init_script(
                """Object.defineProperties(HTMLDialogElement.prototype, {
                    showModal: {configurable: true, value: undefined},
                    close: {configurable: true, value: undefined}
                });"""
            )
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            assert page.evaluate("taskModalHasNativeApi") is False
            trigger = page.get_by_role("button", name="New task")
            dialog = page.get_by_role("dialog", name="Create a task")
            close = dialog.get_by_role("button", name="Close task dialog")
            cancel = dialog.get_by_role("button", name="Cancel", exact=True)
            help_button = page.locator("#help-fab")
            help_panel = page.locator("#help-panel")
            page.evaluate(
                """() => {
                    for (const sheet of document.styleSheets) {
                        for (const rule of sheet.cssRules) {
                            if (rule.selectorText === '.modal') rule.style.removeProperty('inset');
                        }
                    }
                }"""
            )

            help_button.click()
            assert help_panel.is_visible()
            trigger.click()
            assert dialog.is_visible()
            bounds = dialog.bounding_box()
            viewport = page.viewport_size
            assert bounds == {
                "x": 0,
                "y": 0,
                "width": viewport["width"],
                "height": viewport["height"],
            }
            assert dialog.get_attribute("aria-modal") == "true"
            title = dialog.get_by_label("Task title", exact=True)
            assert title.evaluate("el => el === document.activeElement")
            assert help_button.get_attribute("aria-hidden") == "true"
            assert help_button.evaluate("el => getComputedStyle(el).pointerEvents") == "none"
            assert help_panel.get_attribute("aria-hidden") == "true"
            assert page.evaluate(
                """() => {
                    const panel = document.getElementById('help-panel');
                    const rect = panel.getBoundingClientRect();
                    const top = document.elementFromPoint(
                        rect.left + rect.width / 2, rect.top + rect.height / 2
                    );
                    return top === taskModal || taskModal.contains(top);
                }"""
            )
            page.evaluate(
                """() => {
                    const help = document.getElementById('help-fab');
                    help.removeAttribute('inert');
                    help.focus();
                }"""
            )
            assert title.evaluate("el => el === document.activeElement")
            page.keyboard.press("Shift+Tab")
            assert close.evaluate("el => el === document.activeElement")
            page.keyboard.press("Shift+Tab")
            assert cancel.evaluate("el => el === document.activeElement")
            page.keyboard.press("Tab")
            assert close.evaluate("el => el === document.activeElement")

            page.keyboard.press("Escape")
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")
            assert help_button.get_attribute("aria-hidden") is None
            assert help_button.evaluate("el => getComputedStyle(el).pointerEvents") != "none"
            assert help_panel.get_attribute("aria-hidden") is None
            assert help_panel.is_visible()
            help_panel.get_by_role("button", name="Close").click()

            page.evaluate(
                """() => {
                    anthillTour.start();
                    window.testTourCard = document.querySelector('.tour-title').parentElement.parentElement;
                    openTaskModal(document.querySelector('button[onclick*="openTaskModal"]'));
                }"""
            )
            tour_step = page.locator(".tour-count").inner_text()
            assert page.evaluate(
                """() => {
                    const card = window.testTourCard;
                    card.removeAttribute('inert');
                    card.style.setProperty('pointer-events', 'auto', 'important');
                    const rect = card.getBoundingClientRect();
                    const top = document.elementFromPoint(
                        rect.left + rect.width / 2, rect.top + rect.height / 2
                    );
                    return top === taskModal || taskModal.contains(top);
                }"""
            )
            page.keyboard.press("Enter")
            assert dialog.is_visible()
            assert page.locator(".tour-count").inner_text() == tour_step
            page.keyboard.press("Escape")
            assert dialog.is_hidden()
            assert page.locator(".tour-count").inner_text() == tour_step
            page.locator(".tour-skip").click()

            trigger.click()
            close.click()
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")

            trigger.click()
            cancel.click()
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")

            trigger.click()
            dialog.click(position={"x": 5, "y": 5})
            assert dialog.is_hidden()
            assert trigger.evaluate("el => el === document.activeElement")
        finally:
            browser.close()


def test_tasks_ui_creates_and_edits_in_browser_timezone(live):
    base, token, conv_id, _asst_id = live
    timezone_id = "America/Los_Angeles"
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id, timezone_id=timezone_id)
        try:
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            page.get_by_role("button", name="New task").click()
            form = page.locator("#task-form")
            assert form.locator("#task-timezone").input_value() == timezone_id
            assert form.locator("#task-timezone-note").is_visible() is False
            form.locator('[name="title"]').fill("Browser timezone task")
            form.locator('[name="goal"]').fill("Summarize browser timezone behavior")
            form.locator('[name="schedule"]').select_option("daily")
            assert form.locator("#task-timezone-note").is_visible() is True
            assert form.locator("#task-timezone-label").inner_text() == timezone_id
            form.get_by_role("button", name="Create task").click()

            row = page.locator("tr", has_text="Browser timezone task")
            row.wait_for()
            assert timezone_id in row.inner_text()

            edit_context = browser.new_context(
                service_workers="block", timezone_id="America/New_York"
            )
            edit_context.add_cookies([{"name": "session_token", "value": token, "url": base}])
            edit_page = edit_context.new_page()
            edit_page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            edit_row = edit_page.locator("tr", has_text="Browser timezone task")
            edit_row.get_by_role("button", name=re.compile(r"^Edit task: ")).click()
            edit_form = edit_page.locator("#task-form")
            assert edit_form.locator("#task-timezone-label").inner_text() == timezone_id
            assert edit_form.locator("#task-timezone-note").is_visible() is True
            edit_form.locator('[name="title"]').fill("Edited browser timezone task")
            edit_form.locator('[name="schedule"]').select_option("hourly")
            assert edit_form.locator("#task-timezone-note").is_visible() is False
            edit_form.locator('[name="schedule"]').select_option("weekly")
            assert edit_form.locator("#task-timezone-note").is_visible() is True
            edit_form.get_by_role("button", name="Save changes").click()

            edited_row = edit_page.locator("tr", has_text="Edited browser timezone task")
            edited_row.wait_for()
            assert "weekly" in edited_row.inner_text()
            assert timezone_id in edited_row.inner_text()

            import anthill.web.app as app_mod
            from anthill.web.db import ScheduledTask, TaskOccurrence, TaskRun

            s = app_mod._SessionFactory()
            task = (
                s.query(ScheduledTask)
                .filter(ScheduledTask.title == "Edited browser timezone task")
                .one()
            )
            assert task.schedule == "weekly"
            assert task.timezone == timezone_id

            edit_row = edit_page.locator("tr", has_text="Edited browser timezone task")
            edit_row.get_by_role("button", name=re.compile(r"^Edit task: ")).click()
            edit_form.locator("#task-timezone").fill("Europe/Paris")
            edit_form.get_by_role("button", name="Save changes").click()
            edited_row = edit_page.locator("tr", has_text="Edited browser timezone task")
            edited_row.wait_for()
            assert "Europe/Paris" in edited_row.inner_text()
            s.expire_all()
            task = s.get(ScheduledTask, task.id)
            assert task.timezone == "Europe/Paris"

            from datetime import datetime, timezone

            claimed_at = datetime.now(timezone.utc)
            active_occurrence = TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="manual",
                status="claimed",
                due_at=claimed_at,
                inputs="[]",
            )
            s.add(active_occurrence)
            s.flush()
            active_run = TaskRun(
                org_id=task.org_id,
                task_id=task.id,
                occurrence_id=active_occurrence.id,
                status="running",
                scheduled_for=claimed_at,
                started_at=claimed_at,
            )
            s.add(active_run)
            s.flush()
            active_occurrence.claimed_run_id = active_run.id
            task.status = "running"
            s.commit()
            edit_page.reload(wait_until="domcontentloaded")
            running_row = edit_page.locator("tr", has_text="Edited browser timezone task")
            assert running_row.get_by_role("button", name=re.compile(r"^Edit task: ")).is_visible()

            legacy_hourly = ScheduledTask(
                org_id=task.org_id,
                created_by=task.created_by,
                title="Legacy hourly task",
                goal="g",
                schedule=" Hourly ",
                timezone=timezone_id,
                status="pending",
            )
            s.add(legacy_hourly)
            s.commit()
            edit_page.reload(wait_until="domcontentloaded")
            legacy_row = edit_page.locator("tr", has_text="Legacy hourly task")
            assert timezone_id not in legacy_row.inner_text()
            legacy_row.get_by_role("button", name=re.compile(r"^Edit task: ")).click()
            assert edit_page.locator("#task-timezone-note").is_hidden()
        finally:
            browser.close()


def test_streaming_shows_stop_and_send_queues(live):
    """While a reply streams, Stop is its own button and the send button stays a send button,
    so typing a follow-up and sending it queues the message instead of forcing a stop."""
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector("#send-btn")
            res = page.evaluate(
                """() => {
                    setStreaming(true);
                    const css = id => getComputedStyle(document.getElementById(id)).display;
                    const out = {
                        stopVisible: css('stop-btn') !== 'none',
                        sendIcon: document.getElementById('send-icon').textContent.trim(),
                    };
                    document.getElementById('msg-input').value = 'a follow-up';
                    sendMessage();
                    out.queued = promptQueue.length;
                    out.queueShown = css('prompt-queue') !== 'none';
                    out.stillStreaming = streaming;   // queuing must not stop the stream
                    return out;
                }"""
            )
            assert res["stopVisible"] is True  # Stop is available as its own control...
            assert res["sendIcon"] == "➤"  # ...and the send button is still a send (not a stop)
            assert res["queued"] == 1  # the follow-up was queued, not dropped
            assert res["queueShown"] is True
            assert res["stillStreaming"] is True  # and the in-flight reply kept streaming
        finally:
            browser.close()


def test_chat_rail_merges_conversations_and_folds_groups(live):
    """Full-merge chat nav: the conversation list lives in the global rail under Workspace, and the
    secondary nav folds into one collapsed "More" so the work is the focus. On a workspace page the
    secondary items start hidden inside "More"; opening it reveals them - the items are only hidden,
    never removed, so nothing is lost."""
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector("nav.chat-mode")
            # the conversation list moved into the rail; the separate column is gone
            assert page.locator("nav.chat-mode .rail-conv").count() >= 1
            assert page.locator(".chat-sidebar").count() == 0
            assert (
                page.locator("nav.chat-mode .rail-conv.active").count() == 1
            )  # active highlighted
            # the below-cut setup/admin items fold into one collapsed "More"; hidden until it opens.
            # (Knowledge/wiki is a primary work surface now, so it stays in the main block, not here -
            # use a setup item like Solo settings as the folded example.)
            more = page.locator("nav.chat-mode .nav-more").first
            folded = more.locator("a[href='/personalize']")
            assert folded.count() == 1  # the item is in the DOM (tucked away, not removed)
            assert folded.is_visible() is False  # ...but hidden until "More" is opened
            more.locator("summary").first.click()
            assert folded.is_visible() is True
        finally:
            browser.close()


def test_options_panel_hidden_by_default(live):
    """P4: chat is a single box - the Web-search + scope overrides live under an Options disclosure.
    The Agent-mode toggle is retired (#421): the router picks depth, so it is gone entirely."""
    base, token, conv_id, _asst_id = live
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, base, token, conv_id)
        try:
            page.wait_for_selector("#opt-toggle")
            # the Web-search override is off the default surface...
            assert page.locator("#opt-panel").is_visible() is False
            assert page.locator("#opt-web").is_visible() is False
            # ...and the manual "Agent mode" toggle no longer exists at all (#421)
            assert page.locator("#opt-agent").count() == 0
            # Options reveals the remaining overrides (web search + knowledge scope)
            page.locator("#opt-toggle").click()
            assert page.locator("#opt-panel").is_visible() is True
            assert page.locator("#opt-web").is_visible() is True
        finally:
            browser.close()
