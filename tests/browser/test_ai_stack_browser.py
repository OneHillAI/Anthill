"""Browser checks for the shared "where it runs / which model / inference provider" layout
(docs/specs/ai-stack-guided-layout.md): the selected tier card opens into the panel that holds its models, the
panel changes with the tier, the inference provider stays separate and defaults to "Don't use one", and the
advanced choices stay collapsed. Skipped when Playwright is not installed.
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


def _launch_chromium(playwright):
    return playwright.chromium.launch(channel="chrome" if os.environ.get("CI") else None)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    d = tmp_path_factory.mktemp("aistack")
    saved = {
        k: os.environ.get(k) for k in ("ANTHILL_DB", "ANTHILL_JWT_SECRET", "ANTHILL_ENCRYPTION_KEY")
    }
    os.environ["ANTHILL_DB"] = str(d / "a.db")
    os.environ["ANTHILL_JWT_SECRET"] = "browser-test-secret-0123456789abcdef"
    os.environ["ANTHILL_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

    eng = create_engine(f"sqlite:///{d / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo", local_model_chosen=False)])
    s.commit()
    token = make_token(u.id, o.id, "admin")
    org_id = int(o.id)
    s.close()

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

    yield f"http://127.0.0.1:{port}", token, app_mod, org_id

    server.should_exit = True
    thread.join(timeout=5)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _set_compute(app_mod, org_id, value):
    from anthill.web.db import OrgSettings

    session = app_mod._SessionFactory()
    session.query(OrgSettings).filter(OrgSettings.org_id == org_id).one().solo_compute = value
    session.commit()
    session.close()


def _open(playwright, live, path, width=1180, javascript=True):
    base, token, _, _ = live
    browser = _launch_chromium(playwright)
    ctx = browser.new_context(
        viewport={"width": width, "height": 900},
        service_workers="block",
        java_script_enabled=javascript,
    )
    ctx.add_cookies([{"name": "session_token", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(f"{base}{path}", wait_until="domcontentloaded")
    page.wait_for_selector("#cc-attach", state="attached")
    return browser, page


def _box(page, selector):
    return page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect();"
        " return {l: r.left, r: r.right, t: r.top, b: r.bottom}; }",
        selector,
    )


def _seam(card, panel):
    """The selected card covers the panel's 3px top border: its bottom is 2 to 4px below the panel's top."""
    return 2 <= card["b"] - panel["t"] <= 4


def _seam_is_really_open(page):
    """The selected card has no bottom border of its own and is what you hit at the panel's top edge, under the
    card's middle. Position alone is not enough: any card overlaps the panel by its margin."""
    return page.evaluate(
        """() => {
            const card = document.querySelector('.cc-tier.sel');
            const panel = document.querySelector('#cc-attach').getBoundingClientRect();
            const box = card.getBoundingClientRect();
            const hit = document.elementFromPoint((box.left + box.right) / 2, panel.top + 1);
            return {
                border: getComputedStyle(card).borderBottomWidth,
                radius: getComputedStyle(card).borderBottomLeftRadius,
                covered: !!hit && !!hit.closest('.cc-tier.sel'),
            };
        }"""
    )


def _inside_panel(page, selector):
    return page.evaluate("(s) => !!document.querySelector(s).closest('#cc-attach')", selector)


def test_machine_models_sit_inside_the_panel_that_the_selected_card_opens_into(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            assert page.locator("#council-local").is_visible()
            assert not page.locator("#council-cloud").is_visible()
            assert not page.locator("#cc-connect-cloud").is_visible()
            assert _inside_panel(page, "#council-local")
            card, panel = _box(page, ".cc-tier.sel"), _box(page, "#cc-attach")
            assert page.locator(".cc-tier.sel").get_attribute("data-tier") == "local"
            assert (
                abs(card["l"] - panel["l"]) <= 1
            )  # the card is a tab: same left edge as the panel
            assert _seam(card, panel)
            r = _seam_is_really_open(page)
            assert r["border"] == "0px" and r["covered"], r
        finally:
            browser.close()


def test_cloud_opens_its_own_provider_key_and_models_inside_the_panel(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            page.click('.cc-tier[data-tier="cloud"]')
            assert page.locator("#cc-connect-cloud").is_visible()
            assert page.locator("#council-cloud").is_visible()
            assert not page.locator("#council-local").is_visible()
            assert _inside_panel(page, "#cc-connect-cloud")
            assert _inside_panel(page, "#council-cloud")
            page.click('#cc-provs .cc-prov[data-key="runpod"]')
            assert page.locator("#cc-key").is_visible()
            assert _inside_panel(page, "#cc-key")
            card, panel = _box(page, ".cc-tier.sel"), _box(page, "#cc-attach")
            assert page.locator(".cc-tier.sel").get_attribute("data-tier") == "cloud"
            assert abs(card["r"] - panel["r"]) <= 1  # the right-hand tab ends where the panel ends
            assert _seam(card, panel)
            r = _seam_is_really_open(page)
            assert r["border"] == "0px" and r["covered"], r
            assert page.locator("#cc-tierseg").get_attribute("data-sel") == "cloud"
        finally:
            browser.close()


def test_the_inference_provider_is_not_attached_to_the_tier_and_defaults_to_none(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            assert not _inside_panel(page, ".cc-infer-card")
            assert _box(page, ".cc-infer-card")["t"] > _box(page, "#cc-attach")["b"]
            assert "sel" in page.locator("#cc-escalation-provs .cc-prov.none").get_attribute(
                "class"
            )
            assert page.locator("#cc-escalation-provider").input_value() == ""
            before = _box(page, ".cc-infer-card")["t"] - _box(page, "#cc-attach")["b"]
            page.click('.cc-tier[data-tier="cloud"]')
            after = _box(page, ".cc-infer-card")["t"] - _box(page, "#cc-attach")["b"]
            assert (
                page.locator("#cc-escalation-provider").input_value() == ""
            )  # untouched by the tier
            assert (
                abs(before - after) < 1
            )  # still a separate card at the same distance from the panel
        finally:
            browser.close()


def test_continue_waits_for_a_model_and_posts_the_same_values(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            assert page.locator("#mp-continue").is_disabled()
            first = page.locator("#council-local input[name='council_models']").first
            first.check()
            assert page.locator("#mp-continue").is_enabled()
            ticked = first.input_value()
            posted = page.evaluate(
                "() => Object.fromEntries(new FormData("
                "document.querySelector('form[action=\"/setup/model\"]')).entries())"
            )
            assert posted["compute"] == "local"
            assert posted["council_models"] == ticked and posted["council_lead"] == ticked
            assert posted["enable_vision"] == "on" and "vision_max_accuracy" not in posted
            assert (
                posted["escalation_provider"] == ""
            )  # "Don't use one" is what is posted by default
        finally:
            browser.close()


def test_advanced_is_collapsed_and_holds_the_vision_choices(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            assert not page.locator("details.cc-adv").evaluate("d => d.open")
            assert not page.locator("input[name='enable_vision']").is_visible()
            page.click("details.cc-adv summary")
            assert page.locator("input[name='enable_vision']").is_visible()
            assert page.locator("input[name='enable_vision']").is_checked()
            assert not page.locator("input[name='vision_max_accuracy']").is_checked()
        finally:
            browser.close()


def test_the_vision_summary_follows_the_checkbox(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            assert page.locator("#cc-vision-state").inner_text() == "Image understanding on"
            page.click("details.cc-adv summary")
            page.uncheck("input[name='enable_vision']")
            assert page.locator("#cc-vision-state").inner_text() == "Image understanding off"
            page.check("input[name='enable_vision']")
            assert page.locator("#cc-vision-state").inner_text() == "Image understanding on"
        finally:
            browser.close()


def test_narrow_window_keeps_the_cards_in_place_as_tabs_over_the_panel(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model", width=620)
        try:
            machine = _box(page, '.cc-tier[data-tier="local"]')
            cloud = _box(page, '.cc-tier[data-tier="cloud"]')
            # side by side in the same order as the Tab order, and they stay put when the other is clicked
            assert abs(machine["t"] - cloud["t"]) <= 1 and machine["l"] < cloud["l"]
            for tier in ("local", "cloud", "local"):
                page.click(f'.cc-tier[data-tier="{tier}"]')
                now_m = _box(page, '.cc-tier[data-tier="local"]')
                now_c = _box(page, '.cc-tier[data-tier="cloud"]')
                assert abs(now_m["l"] - machine["l"]) <= 1 and abs(now_c["l"] - cloud["l"]) <= 1
                assert abs(now_m["t"] - machine["t"]) <= 1 and abs(now_c["t"] - cloud["t"]) <= 1
                assert _seam(_box(page, ".cc-tier.sel"), _box(page, "#cc-attach")), tier
                r = _seam_is_really_open(page)
                assert r["border"] == "0px" and r["covered"], (tier, r)
            # the privacy line stays, the long copy goes
            privacy = ".cc-tier.sel .cc-boundary-cap-shared, .cc-tier.sel .cc-boundary-cap"
            assert page.locator(privacy).first.is_visible()
            assert not page.locator(".cc-tier.sel .cc-fit").is_visible()
        finally:
            browser.close()


def test_the_selected_card_is_announced_and_a_focused_card_is_drawn_above_the_panel(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:

            def pressed():
                return [
                    page.locator(f'.cc-tier[data-tier="{t}"]').get_attribute("aria-pressed")
                    for t in ("local", "cloud")
                ]

            assert pressed() == ["true", "false"]
            assert page.locator("#cc-attach").get_attribute("aria-labelledby") == "cc-name-local"
            page.click('.cc-tier[data-tier="cloud"]')
            assert pressed() == ["false", "true"]
            assert page.locator("#cc-attach").get_attribute("aria-labelledby") == "cc-name-cloud"
            page.focus('.cc-tier[data-tier="local"]')
            page.keyboard.press("Shift+Tab")
            page.keyboard.press("Tab")  # :focus-visible comes from the keyboard
            focused_z = page.evaluate(
                "() => getComputedStyle(document.querySelector('.cc-tier:focus-visible')).zIndex"
            )
            panel_z = page.evaluate(
                "() => getComputedStyle(document.querySelector('#cc-attach')).zIndex"
            )
            assert int(focused_z) > int(panel_z)
        finally:
            browser.close()


def test_an_account_saved_on_your_cloud_opens_on_the_cloud_card_with_and_without_script(live):
    _, _, app_mod, org_id = live
    _set_compute(app_mod, org_id, "cloud")
    try:
        with sync_api.sync_playwright() as p:
            # no script at all: the server already marks the cloud card and shows the cloud content
            browser, page = _open(p, live, "/personalize", javascript=False)
            try:
                cloud_classes = page.locator('.cc-tier[data-tier="cloud"]').get_attribute("class")
                assert cloud_classes.split() == ["cc-tier", "sel"]
                assert (
                    page.locator('.cc-tier[data-tier="local"]').get_attribute("aria-pressed")
                    == "false"
                )
                assert page.locator("#cc-tierseg").get_attribute("data-sel") == "cloud"
                assert page.locator("#council-local").get_attribute("hidden") is not None
                assert page.locator("#council-cloud").get_attribute("hidden") is None
                # and what would be posted is the cloud list, not the hidden machine list
                assert page.locator("input.csel-cloud:not([disabled])").count() > 0
                assert page.locator("input.csel-local:not([disabled])").count() == 0
            finally:
                browser.close()
            # with script nothing flips: still the cloud card, its provider area and its models
            browser, page = _open(p, live, "/personalize")
            try:
                page.evaluate("toggleChangeRuns(true)")
                assert (
                    page.locator('.cc-tier[data-tier="cloud"]').get_attribute("aria-pressed")
                    == "true"
                )
                assert page.locator("#cc-connect-cloud").is_visible()
                assert page.locator("#council-cloud").is_visible()
                assert not page.locator("#council-local").is_visible()
                assert _seam(_box(page, ".cc-tier.sel"), _box(page, "#cc-attach"))
                r = _seam_is_really_open(page)
                assert r["border"] == "0px" and r["covered"], r
            finally:
                browser.close()
    finally:
        _set_compute(app_mod, org_id, "local")


def test_settings_opens_the_same_panel(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/personalize")
        try:
            page.evaluate("toggleChangeRuns(true)")
            assert page.locator("#change-runs").is_visible()
            assert _inside_panel(page, "#council-local")
            assert _seam(_box(page, ".cc-tier.sel"), _box(page, "#cc-attach"))
            r = _seam_is_really_open(page)
            assert r["border"] == "0px" and r["covered"], r
            assert not _inside_panel(page, ".cc-infer-card")
            page.click('.cc-tier[data-tier="cloud"]')
            assert _inside_panel(page, "#cc-connect-cloud")
            assert page.locator("#council-cloud").is_visible()
        finally:
            browser.close()


def test_cloud_tier_posts_the_cloud_values(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            page.click('.cc-tier[data-tier="cloud"]')
            page.click('#cc-provs .cc-prov[data-key="runpod"]')
            page.fill("#cc-key", "not-a-real-key")
            first = page.locator("#council-cloud input[name='council_models']").first
            first.check()
            ticked = first.input_value()
            posted = page.evaluate(
                "() => Object.fromEntries(new FormData("
                "document.querySelector('form[action=\"/setup/model\"]')).entries())"
            )
            assert posted["compute"] == "cloud" and posted["cloud_provider"] == "runpod"
            assert posted["provider_api_key"] == "not-a-real-key"
            assert posted["council_models"] == ticked and posted["council_lead"] == ticked
            assert (
                page.locator("#council-local input[name='council_models']:not([disabled])").count()
                == 0
            )
        finally:
            browser.close()


def test_a_card_is_chosen_with_enter_and_with_space(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            for key in ("Enter", " "):
                page.focus('.cc-tier[data-tier="cloud"]')
                page.keyboard.press(key)
                assert page.locator("#cc-tierseg").get_attribute("data-sel") == "cloud", key
                assert (
                    page.locator('.cc-tier[data-tier="cloud"]').get_attribute("aria-pressed")
                    == "true"
                )
                page.focus('.cc-tier[data-tier="local"]')
                page.keyboard.press(key)
                assert page.locator("#cc-tierseg").get_attribute("data-sel") == "local", key
        finally:
            browser.close()


def test_the_vision_summary_matches_a_restored_checkbox(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model")
        try:
            page.evaluate("document.querySelector('input[name=\"enable_vision\"]').checked = false")
            page.evaluate("window.dispatchEvent(new Event('pageshow'))")
            assert page.locator("#cc-vision-state").inner_text() == "Image understanding off"
        finally:
            browser.close()


def test_the_compact_cards_still_say_what_stays_and_what_is_sent(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model", width=620)
        try:
            machine = page.locator('.cc-tier[data-tier="local"]').inner_text()
            cloud = page.locator('.cc-tier[data-tier="cloud"]').inner_text()
            assert "Model and knowledge base" in machine and "Stays on this device" in machine
            assert "Model:" in cloud and "Stays in your cloud account" in cloud
            assert "Knowledge base:" in cloud and "Sent to your cloud when used" in cloud
        finally:
            browser.close()


def test_an_account_saved_on_your_cloud_with_no_provider_yet_gets_the_provider_cards(live):
    _, _, app_mod, org_id = live
    _set_compute(app_mod, org_id, "cloud")  # saved on Your cloud, no cloud provider chosen
    try:
        with sync_api.sync_playwright() as p:
            browser, page = _open(p, live, "/personalize")
            try:
                page.evaluate("toggleChangeRuns(true)")
                provs = page.locator("#cc-provs .cc-prov")
                assert provs.count() == 2  # RunPod and Lambda, drawn by the script
                assert page.locator("#cc-provs .cc-prov.sel").count() == 0  # none picked for you
                assert page.locator("#cc-provider").input_value() == ""
            finally:
                browser.close()
    finally:
        _set_compute(app_mod, org_id, "local")


def test_the_full_cards_show_the_box_and_the_fit_sentence_and_no_subject_labels(live):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, live, "/setup/model", width=1180)
        try:
            for selector in (".cc-fit", ".cc-boundary-items"):
                items = page.locator(selector)
                assert items.count() == 2, selector
                assert all(items.nth(i).is_visible() for i in range(2)), selector
            # the box above the privacy lines names their subject, so the lines do not repeat it
            subjects = page.locator(".cc-subj")
            assert subjects.count() == 3
            assert not any(subjects.nth(i).is_visible() for i in range(3))
        finally:
            browser.close()
