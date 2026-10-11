"""Browser checks: the "check with your inference provider" offer is one quiet link, not a card
(docs/specs/chat-escalation-offer-quiet.md).

After an answer the page offers a check with the attached inference provider. It used to open a big "This needs
more than your local model" card after every reply, even "hi". It is now one low-key button under the answer;
only a click opens the question that asks to approve sending anything onward (until the person has chosen
"Always"). Nothing is sent before a click, one offer sends at most one request, and the request carries the
right `remember`. The stream and the provider call are faked, so no model and no network are needed. Runs in its
own CI job; skipped without Playwright (the `tests/browser` tests do not run without it).
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
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("chatoffer")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

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
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# A stand-in for the browser's EventSource that the test feeds by hand, so the stream can be held open after
# the first word and the page inspected while it is still open (a fulfilled response arrives all at once).
_HAND_FED_STREAM = """
window.__streams = [];
window.EventSource = class {
  constructor(url) { this.url = url; this.readyState = 1; window.__streams.push(this); }
  close() { this.readyState = 2; }
};
window.__push = (data) => {
  const es = window.__streams[window.__streams.length - 1];
  es.onmessage({data: typeof data === 'string' ? data : JSON.stringify(data)});
};
"""


def _page(p, base, token, conv_id, init_script=None):
    browser = p.chromium.launch(channel="chrome" if os.environ.get("CI") else None)
    ctx = browser.new_context(service_workers="block")
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    if init_script:
        ctx.add_init_script(init_script)
    page = ctx.new_page()
    page.goto(f"{base}/chat/{conv_id}", wait_until="domcontentloaded")
    page.wait_for_function("typeof stageText === 'function' && typeof sendMessage === 'function'")
    return browser, page


_OFFER = (
    "window.__push({token: 'Hello!'});"
    "window.__push({escalation_offer: {provider: 'infercom', provider_label: 'Infercom',"
    " consented: %s, message_id: 7}});"
    "window.__push('[DONE]');"
)

_TRIGGER = ".escalation-offer .escalation-offer-trigger"
_OK = '{"ok": true, "text": "The expert says hello."}'


def _ask(page, consented):
    page.fill("#msg-input", "hi")
    page.click("#send-btn")
    page.wait_for_function("window.__streams.length === 1")
    page.evaluate(_OFFER % ("true" if consented else "false"))
    page.wait_for_selector(".escalation-offer")


def _offer(page, consented, with_redo=False):
    """Render an offer directly under a fake local answer (no stream needed)."""
    page.evaluate(
        """([consented, withRedo]) => {
            const answer = appendMsg('assistant', 'A local answer.');
            if (withRedo) {
                const b = document.createElement('button');
                b.title = 'Redo with Infercom';
                b.id = 'redo-sibling';
                answer.parentElement.appendChild(b);
            }
            renderEscalationOffer(answer, {provider_label: 'Infercom', consented, message_id: 7}, 'A local answer.');
        }""",
        [consented, with_redo],
    )
    page.wait_for_selector(_TRIGGER)


def _record(page, body=_OK, hold=False):
    """Answer escalate-confirm with `body` (or never answer when `hold`), and return the list of request bodies."""
    calls: list[str] = []

    def handler(route):
        calls.append(route.request.post_data or "")
        if not hold:
            route.fulfill(status=200, headers={"content-type": "application/json"}, body=body)

    page.route("**/escalate-confirm", handler)
    return calls


def _page_for(p, live):
    base, token, conv_id = live
    return _page(p, base, token, conv_id, _HAND_FED_STREAM)


def test_the_offer_is_one_quiet_button_and_asks_before_first_use(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            calls = _record(page)
            _ask(page, consented=False)
            msg = page.locator(".msg.assistant", has_text="Hello!")
            trigger = msg.locator(_TRIGGER)
            assert trigger.inner_text().strip() == "⚡ Check with Infercom"
            assert (
                trigger.evaluate("e => e.tagName") == "BUTTON"
            )  # a real button: Space and Enter work
            # no card, no "needs more" claim, nothing asked and nothing sent yet
            assert page.locator(".escalation-offer .proposal").count() == 0
            assert "needs more than your local model" not in page.locator("body").inner_text()
            assert calls == []

            # a click opens the question (still nothing sent); "Not now" removes only the offer
            trigger.click()
            assert page.locator(".escalation-offer .proposal").is_visible()
            assert page.locator(".escalation-offer button").all_inner_texts() == [
                "Always",
                "Just this once",
                "Not now",
            ]
            assert calls == []
            page.locator(".escalation-offer .btn-cancel").click()
            assert page.locator(".escalation-offer").count() == 0
            assert page.locator(".msg.assistant", has_text="Hello!").count() == 1
            assert calls == []
        finally:
            browser.close()


def test_once_trusted_the_link_checks_straight_away_and_sends_remember_true(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            calls = _record(page)
            _ask(page, consented=True)
            page.locator(_TRIGGER).click()
            page.wait_for_selector(".msg.assistant:has-text('The expert says hello.')")
            assert page.locator(".escalation-offer").count() == 0
            assert page.locator(".msg.assistant", has_text="Hello!").count() == 1
            assert len(calls) == 1
            assert "message_id=7" in calls[0] and "remember=true" in calls[0]
        finally:
            browser.close()


def test_a_second_activation_while_the_provider_answers_sends_nothing_more(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            calls = _record(
                page, hold=True
            )  # the provider call stays open, as it can for 30 s or more
            _offer(page, consented=True)
            # two activations of the same control, back to back (a double click, or Enter then Space)
            page.evaluate(
                "() => { const b = document.querySelector('.escalation-offer-trigger'); b.click(); b.click(); }"
            )
            page.wait_for_selector(".escalation-offer .build-row.escalating")
            assert page.locator(_TRIGGER).count() == 0  # nothing left to press while it is working
            page.wait_for_timeout(300)
            assert len(calls) == 1
        finally:
            browser.close()


def test_just_this_once_sends_remember_false_and_always_remembers(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            calls = _record(page)
            _offer(page, consented=False)
            page.locator(_TRIGGER).click()
            page.locator(".escalation-offer .btn-once").click()
            page.wait_for_selector(".msg.assistant:has-text('The expert says hello.')")
            assert len(calls) == 1 and "remember=false" in calls[0]
            assert (
                page.evaluate("ESCALATION_CONSENTED") is False
            )  # "just this once" decides nothing for later

            _offer(page, consented=False)
            page.locator(_TRIGGER).click()
            page.locator(".escalation-offer .btn-doit").click()
            page.wait_for_function("document.querySelectorAll('.msg.assistant').length >= 2")
            page.wait_for_function("ESCALATION_CONSENTED === true")
            assert len(calls) == 2 and "remember=true" in calls[1] and "message_id=7" in calls[1]
        finally:
            browser.close()


def test_a_checked_answer_loses_its_redo_with_provider_control(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            _record(page)
            _offer(page, consented=True, with_redo=True)
            assert page.locator("#redo-sibling").count() == 1
            page.locator(_TRIGGER).click()
            page.wait_for_selector(".msg.assistant:has-text('The expert says hello.')")
            assert page.locator("#redo-sibling").count() == 0
        finally:
            browser.close()


def test_the_error_paths_say_what_went_wrong(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            _record(page, body='{"ok": false, "error": "Monthly limit reached."}')
            _offer(page, consented=True)
            page.locator(_TRIGGER).click()
            page.wait_for_selector(".escalation-offer:has-text('Monthly limit reached.')")
            assert page.locator(".msg.assistant", has_text="A local answer.").count() >= 1

            page.unroute("**/escalate-confirm")
            page.route("**/escalate-confirm", lambda route: route.abort())
            _offer(page, consented=True)
            page.locator(".escalation-offer-trigger").click()
            page.wait_for_selector(".escalation-offer:has-text('could not reach the server')")
        finally:
            browser.close()


def test_keyboard_users_land_in_the_question_and_can_use_the_button(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            _record(page)
            _offer(page, consented=False)
            page.locator(_TRIGGER).focus()
            page.keyboard.press("Space")  # a real button: Space activates it (a link would not)
            page.wait_for_selector(".escalation-offer .proposal")
            # focus moved into the question instead of falling back to the page
            assert page.evaluate("document.activeElement.textContent") == "Always"
            assert page.locator(".escalation-offer").get_attribute("role") == "group"
        finally:
            browser.close()


def test_checking_through_redo_with_provider_retires_the_offer(live):
    with sync_api.sync_playwright() as p:
        browser, page = _page_for(p, live)
        try:
            _record(page)
            _offer(page, consented=False)
            page.evaluate(
                """() => {
                    const container = document.querySelector('.escalation-offer').parentElement;
                    const redo = document.createElement('button');
                    redo.id = 'redo-btn';
                    container.appendChild(redo);
                    fireRedoWithProvider(redo, container, 7, true);
                }"""
            )
            page.wait_for_selector(".msg.assistant:has-text('The expert says hello.')")
            assert page.locator(".escalation-offer").count() == 0
            assert page.evaluate("ESCALATION_CONSENTED") is True
        finally:
            browser.close()
