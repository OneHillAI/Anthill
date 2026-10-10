"""Browser check (#92): Tasks text and controls meet WCAG AA contrast in light and dark themes.

A headless Chromium opens the Tasks list and a task result page in forced light, forced dark and the system
dark preference, and measures the contrast of every piece of text and every control boundary on the page from
the computed styles (background colours are composited over their ancestors, so translucent badge tints are
measured as drawn). Normal text needs 4.5:1, large text and non-text controls need 3:1. Runs in its own CI
job; skipped when Playwright is not installed.
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

# Runs in the page. Returns [{selector, text, ratio, need, fg, bg}] for everything that falls short.
AUDIT_JS = r"""
() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const over = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return {r: 0, g: 0, b: 0, a: 0};
    const mix = (t, b) => (t * top.a + b * bottom.a * (1 - top.a)) / a;
    return {r: mix(top.r, bottom.r), g: mix(top.g, bottom.g), b: mix(top.b, bottom.b), a};
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const backdrop = (el) => {
    const stack = [];
    for (let n = el; n; n = n.parentElement) {
      const bg = parse(getComputedStyle(n).backgroundColor);
      if (bg && bg.a > 0) { stack.push(bg); if (bg.a >= 1) break; }
    }
    let out = {r: 255, g: 255, b: 255, a: 1};
    if (stack.length && stack[stack.length - 1].a < 1) out = parse(getComputedStyle(document.documentElement).backgroundColor) || out;
    for (let i = stack.length - 1; i >= 0; i--) out = over(stack[i], out);
    return out;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0;
  };
  const name = (el) => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '');
  const bad = [];
  document.querySelectorAll('body *').forEach((el) => {
    if (!visible(el)) return;
    const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ');
    if (!own || el.closest('script, style, noscript, svg, .sidebar, nav')) return;
    const cs = getComputedStyle(el);
    const fg0 = parse(cs.color); if (!fg0) return;
    const bg = backdrop(el);
    const fg = over(fg0, bg);
    const size = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    if (r < need) bad.push({selector: name(el), text: own.slice(0, 40), ratio: Math.round(r * 100) / 100, need,
      fg: cs.color, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`});
  });
  // Form fields are identified by their boundary: it needs 3:1 against what is behind it (WCAG 1.4.11).
  document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea').forEach((el) => {
    if (!visible(el)) return;
    const cs = getComputedStyle(el);
    if (parseFloat(cs.borderTopWidth) < 1) return;
    const bg = backdrop(el.parentElement || el);
    const edge = over(parse(cs.borderTopColor) || {r: 0, g: 0, b: 0, a: 0}, bg);
    const r = ratio(edge, bg);
    if (r < 3) bad.push({selector: name(el) + '[border]', text: el.name || el.id || '', ratio: Math.round(r * 100) / 100, need: 3,
      fg: cs.borderTopColor, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`});
  });
  // Placeholder text is text too: measured against the field's own background.
  document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach((el) => {
    if (!visible(el)) return;
    const cs = getComputedStyle(el), ph = getComputedStyle(el, '::placeholder');
    const fieldBg = over(parse(cs.backgroundColor) || {r: 0, g: 0, b: 0, a: 0}, backdrop(el.parentElement || el));
    const c = parse(ph.color); if (!c) return;
    c.a *= parseFloat(ph.opacity || '1');
    const r = ratio(over(c, fieldBg), fieldBg);
    if (r < 4.5) bad.push({selector: name(el) + '::placeholder', text: el.getAttribute('placeholder').slice(0, 30),
      ratio: Math.round(r * 100) / 100, need: 4.5, fg: ph.color,
      bg: `rgb(${Math.round(fieldBg.r)},${Math.round(fieldBg.g)},${Math.round(fieldBg.b)})`});
  });
  return bad;
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
    """A real uvicorn server with an admin and tasks in every state the Tasks list can show."""
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("taskscontrast")
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
    now = datetime.now(timezone.utc)
    first_id = None
    for status in ("pending", "running", "done", "failed", "cancelled"):
        t = ScheduledTask(
            org_id=o.id,
            created_by=u.id,
            title=f"Task {status}",
            goal="Summarise the week and list the open actions",
            schedule="daily",
            status=status,
            run_count=2,
            last_run_at=now,
            next_run_at=now,
            last_result="## Result\n\nAll good" if status != "failed" else "ERROR: boom",
            verify_needs_review=status == "done",
            verify_reason="The result did not cover the goal",
            cadence_needs_review=status == "pending",
            cadence_review_reason="Schedule changed",
            queued_inputs='["follow up on the budget"]' if status == "pending" else "[]",
            plane="org" if status == "done" else "solo",
        )
        s.add(t)
        s.flush()
        first_id = first_id or t.id
        s.add(
            TaskRun(
                org_id=o.id,
                task_id=t.id,
                trigger="manual",
                status="ok",
                result="done",
                finished_at=now,
                verify_needs_review=True,
                verify_reason="The result did not cover the goal",
                verify_confidence="low",
            )
        )
        s.add(
            TaskRun(
                org_id=o.id,
                task_id=t.id,
                trigger="scheduled",
                status="error",
                error="Boom",
                finished_at=now,
            )
        )
    live_task = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Task live",
        goal="Watch the feed",
        schedule="daily",
        status="running",
    )
    s.add(live_task)
    s.flush()
    s.add(
        TaskRun(
            org_id=o.id, task_id=live_task.id, trigger="manual", status="running", finished_at=None
        )
    )
    s.commit()
    live_id = live_task.id
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

    yield f"http://127.0.0.1:{port}", token, first_id, live_id

    server.should_exit = True
    thread.join(timeout=5)
    mp.undo()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


THEMES = ["light", "dark", "system-dark"]


def _fmt(bad):
    """One line per distinct failure, so a red run says what to fix."""
    seen, lines = set(), []
    for b in bad:
        key = (b["selector"], b["fg"], b["bg"])
        if key not in seen:
            seen.add(key)
            lines.append(
                f"{b['ratio']}:1 (need {b['need']}) {b['selector']} {b['fg']} on {b['bg']} {b['text']!r}"
            )
    return "\n" + "\n".join(lines)


def _audit(live, p, path, theme, open_queue=False):
    base, token = live[0], live[1]
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    try:
        ctx = browser.new_context(
            service_workers="block",
            color_scheme="dark" if theme in ("dark", "system-dark") else "light",
        )
        ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
        ctx.add_init_script(
            "try{localStorage.setItem('anthill-theme','%s')}catch(e){}"
            % (theme if theme != "system-dark" else "system")
        )
        page = ctx.new_page()
        page.goto(f"{base}{path}", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        if theme != "system-dark":
            page.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
        else:
            page.evaluate("document.documentElement.removeAttribute('data-theme')")
        if open_queue:
            page.evaluate(
                "document.querySelectorAll('[id^=q-]').forEach(r => r.style.display = '')"
            )
            page.evaluate("document.getElementById('new-task').showModal()")
            # A daily schedule shows the time zone row (hidden for "Run once"), so its field is measured too.
            page.select_option("#task-schedule", "daily")
            page.evaluate(
                "document.getElementById('task-schedule').dispatchEvent(new Event('change'))"
            )
            # Other field types (a later change adds a datetime-local field): the same border rule must cover them.
            page.evaluate(
                """() => ['datetime-local', 'date', 'time', 'number', 'search', 'email'].forEach((type) => {
                    const i = document.createElement('input'); i.type = type; i.name = 'x-' + type;
                    document.querySelector('#task-form').appendChild(i); })"""
            )
        return page.evaluate(AUDIT_JS)
    finally:
        browser.close()


@pytest.mark.parametrize("theme", THEMES)
def test_tasks_list_text_meets_aa(live, theme):
    with sync_api.sync_playwright() as p:
        bad = _audit(live, p, "/tasks", theme, open_queue=True)
    assert bad == [], _fmt(bad)


@pytest.mark.parametrize("theme", THEMES)
def test_task_result_page_text_meets_aa(live, theme):
    with sync_api.sync_playwright() as p:
        bad = _audit(live, p, f"/tasks/{live[2]}/result", theme)
    assert bad == [], _fmt(bad)


@pytest.mark.parametrize("theme", THEMES)
def test_running_task_result_page_banner_meets_aa(live, theme):
    with sync_api.sync_playwright() as p:
        bad = _audit(live, p, f"/tasks/{live[3]}/result", theme)
    assert bad == [], _fmt(bad)


def test_scope_is_shown_as_a_word_and_by_shape_not_by_colour_alone(live):
    base, token = live[0], live[1]
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            org = page.locator("td:has(.plane-dot-org)").first
            solo = page.locator("td:has(.plane-dot-solo)").first
            assert org.inner_text().strip() == "Org" and solo.inner_text().strip() == "Solo"
            radius = "getComputedStyle(el).borderRadius"
            assert page.locator(".plane-dot-org").first.evaluate(f"el => {radius}") != page.locator(
                ".plane-dot-solo"
            ).first.evaluate(f"el => {radius}")
            assert page.locator(".plane-dot").first.get_attribute("aria-hidden") == "true"
        finally:
            browser.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_focused_field_still_changes_its_border_to_the_accent_colour(live, theme):
    base, token = live[0], live[1]
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
            ctx.add_init_script(
                f"try{{localStorage.setItem('anthill-theme','{theme}')}}catch(e){{}}"
            )
            page = ctx.new_page()
            page.goto(f"{base}/tasks", wait_until="domcontentloaded")
            page.evaluate("document.getElementById('new-task').showModal()")
            field = page.locator("#task-title")
            border = "el => getComputedStyle(el).borderTopColor"
            before = field.evaluate(border)
            field.focus()
            after = field.evaluate(border)
            accent = page.evaluate(
                "(() => { const e = document.createElement('i'); e.style.color = 'var(--accent)';"
                " document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c; })()"
            )
            assert before != after and after == accent, (before, after, accent)
            assert (
                field.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
            )  # the focus ring
            # The time zone field has no type attribute; it must look like the others and keep a visible focus.
            page.select_option("#task-schedule", "daily")
            page.evaluate(
                "document.getElementById('task-schedule').dispatchEvent(new Event('change'))"
            )
            tz = page.locator("#task-timezone")
            assert tz.is_visible()
            tz.focus()
            assert tz.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
            surface = page.evaluate(
                "(() => { const e = document.createElement('i'); e.style.color = 'var(--surface)';"
                " document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c; })()"
            )
            assert tz.evaluate("el => getComputedStyle(el).backgroundColor") == surface
        finally:
            browser.close()
