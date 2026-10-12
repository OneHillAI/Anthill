"""The "Choose your AI" step and Settings' "Change where it runs" share one layout
(docs/specs/ai-stack-guided-layout.md): the selected tier card opens into a panel that holds its own models
(and, for Your cloud, its own provider), the optional inference provider is a separate card after it, and the
advanced choices are collapsed. These read the rendered HTML; tests/browser/test_ai_stack_browser.py drives it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, *, chosen=False, compute="local"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(User(org_id=o.id, email="a@a.com", role="admin", active=True))
    s.add(
        OrgSettings(
            org_id=o.id,
            deployment_topology="solo",
            local_model_chosen=chosen,
            solo_compute=compute,
        )
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c


_VOID = {
    "input",
    "br",
    "hr",
    "img",
    "meta",
    "link",
    "source",
    "wbr",
    "col",
    "area",
    "base",
    "embed",
    "param",
}


def _ids_inside(body: str, root_id: str) -> set[str]:
    """The ids of every element nested inside the element `root_id` (by real tag nesting, not text position)."""

    class _Walk(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack: list[tuple[str, bool]] = []
            self.found: set[str] = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            inside = any(flag for _, flag in self.stack)
            if inside and attrs.get("id"):
                self.found.add(attrs["id"])
            if tag in _VOID:
                return
            self.stack.append((tag, inside or attrs.get("id") == root_id))

        def handle_endtag(self, tag):
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    walk = _Walk()
    walk.feed(body)
    return walk.found


def _panel(body: str) -> str:
    """The text of the tier panel: from its opening tag to the hidden compute input that follows it."""
    start = re.search(r'<div class="cc-attach" id="cc-attach"[^>]*>', body).start()
    return body[start : body.index('<input type="hidden" name="compute"', start)]


def test_setup_puts_the_models_and_the_cloud_provider_inside_the_tier_panel(tmp_path):
    body = _client(tmp_path).get("/setup/model").text
    panel = _panel(body)
    # the panel follows the two cards (the tabs) directly, and holds the cloud provider and both model lists
    tabs_end = re.search(r'<div class="cc-attach" id="cc-attach"[^>]*>', body).start()
    assert 'id="cc-tierseg"' in body[:tabs_end]
    assert body[:tabs_end].rstrip().endswith("</div>")
    inside = _ids_inside(body, "cc-attach")
    assert {"cc-connect-cloud", "cc-key", "council-local", "council-cloud"} <= inside
    assert "cc-tierseg" not in inside and "cc-escalation-provs" not in inside
    for needle in (
        'id="cc-connect-cloud"',
        'id="council-local"',
        'id="council-cloud"',
        'name="provider_api_key"',
    ):
        assert needle in panel, needle
    assert panel.count('name="council_models"') >= 2
    # the inference provider is NOT in the panel: it is its own card after it
    assert 'class="cc-infer-card"' not in panel
    assert body.index('class="cc-infer-card"') > body.index('id="council-cloud"')


def test_the_cards_say_which_one_is_selected_to_assistive_technology(tmp_path):
    body = _client(tmp_path).get("/setup/model").text
    local = re.search(r'<button[^>]*data-tier="local"[^>]*>', body).group(0)
    cloud = re.search(r'<button[^>]*data-tier="cloud"[^>]*>', body).group(0)
    assert 'aria-pressed="true"' in local and 'aria-pressed="false"' in cloud
    assert 'aria-controls="cc-attach"' in local and 'aria-controls="cc-attach"' in cloud
    assert re.search(r'id="cc-attach" role="region" aria-labelledby="cc-name-local"', body)
    assert 'id="cc-name-local"' in body and 'id="cc-name-cloud"' in body
    # one script helper keeps the class, the pressed state, the side of the panel and its name together
    helper = re.search(r"function ccMarkTier\(tier\)\{.*?\n\}", body, re.S).group(0)
    for needle in ("aria-pressed", "dataset.sel", "aria-labelledby", "classList.toggle('sel'"):
        assert needle in helper, needle
    assert (
        body.count("ccMarkTier(") >= 3
    )  # defined once, used when a card is clicked and when state is loaded


def test_an_account_saved_on_your_cloud_renders_the_cloud_card_selected_before_any_script(tmp_path):
    body = _client(tmp_path, chosen=True, compute="cloud").get("/personalize").text
    local = re.search(r'<button[^>]*data-tier="local"[^>]*>', body).group(0)
    cloud = re.search(r'<button[^>]*data-tier="cloud"[^>]*>', body).group(0)
    assert " sel" in cloud and " sel" not in local.split('class="')[1].split('"')[0]
    assert 'aria-pressed="true"' in cloud and 'aria-pressed="false"' in local
    assert 'id="cc-tierseg" data-sel="cloud"' in body
    assert 'aria-labelledby="cc-name-cloud"' in body
    # the panel already shows the cloud's content and hides the machine's
    assert re.search(r'id="cc-connect-cloud"(?![^>]*hidden)', body)
    assert re.search(r'id="council-local"[^>]*hidden', body)
    assert not re.search(r'id="council-cloud"[^>]*hidden', body)


def test_setup_numbers_the_three_steps_and_settings_does_not(tmp_path):
    c = _client(tmp_path)
    setup = c.get("/setup/model").text
    assert setup.count('class="cc-num"') == 3
    for n, title in (
        (1, "Where does your model run?"),
        (2, "Which model do you run?"),
        (3, "Which inference provider to connect?"),
    ):
        assert f'<span class="cc-num">{n}</span>{title}' in setup
    # the model title is inside the panel; the provider title is after it
    assert "Which model do you run?" in _panel(setup)
    assert "Which inference provider to connect?" not in _panel(setup)
    settings = c.get("/personalize").text
    assert '<span class="cc-num">' not in settings


def test_the_inference_provider_is_optional_and_explains_why(tmp_path):
    for name, url in (("setup", "/setup/model"), ("settings", "/personalize")):
        folder = tmp_path / name
        folder.mkdir()
        body = _client(folder, chosen=name == "settings").get(url).text
        assert "Recommended for most people" not in body
        assert '<span class="cc-infer-badge">Optional</span>' in body
        assert (
            "Your model may not be strong enough for your hardest work. "
            "A provider adds frontier-scale open models." in body
        )
        assert "Choose an inference provider to have access to frontier-scale" not in body


def test_the_provider_text_is_as_large_as_the_machine_cards_hardware_line(tmp_path):
    body = _client(tmp_path).get("/setup/model").text
    sub = re.search(r"\.cc-tier-sub\{font-size:(\d+)px", body)
    infer = re.search(r"\.cc-infer-sub\{font-size:(\d+)px", body)
    assert sub and infer and infer.group(1) == sub.group(1)


def test_setup_keeps_the_vision_choices_in_one_collapsed_advanced(tmp_path):
    body = _client(tmp_path).get("/setup/model").text
    details = re.search(r'<details class="cc-adv">(.*?)</details>', body, re.S)
    assert details, "no collapsed Advanced block"
    block = details.group(1)
    # the summary says the (default-on, ~2.4 GB) image download is on, since it is no longer in view
    assert (
        'Advanced <span class="cc-adv-state" id="cc-vision-state">Image understanding on</span>'
        in block
    )
    assert 'name="enable_vision" value="on" checked onchange="ccVisionState()"' in block
    assert "function ccVisionState()" in body
    assert 'name="enable_vision" value="on" checked' in block  # still on by default
    assert 'name="vision_max_accuracy" value="on">' in block  # the larger model stays opt-in
    assert (
        "Self-hosted server or device" in block
    )  # the existing coming-soon rows live in the same one
    assert " open" not in details.group(0)[: details.group(0).index(">")]
    # and the old loose block between the provider card and Continue is gone
    assert body.count('<input type="checkbox" name="enable_vision"') == 1


def test_settings_shows_the_same_panel_under_change_where_it_runs(tmp_path):
    body = _client(tmp_path, chosen=True).get("/personalize").text
    assert "Where does your model run?" not in body  # unchanged: the summary card already says it
    reveal = body[body.index('id="change-runs"') :]
    panel = _panel(reveal)
    assert 'id="council-local"' in panel and "Choose your council" in panel
    assert 'class="cc-infer-card"' in reveal and 'class="cc-infer-card"' not in panel
    assert "<summary>Advanced setups</summary>" in reveal


def _named_controls_inside(body: str, root_id: str) -> list[dict]:
    """Every named input/select/textarea nested inside `root_id`, with its attributes."""

    class _Walk(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack: list[tuple[str, bool]] = []
            self.controls: list[dict] = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            inside = any(flag for _, flag in self.stack)
            if inside and tag in {"input", "select", "textarea"} and attrs.get("name"):
                self.controls.append(attrs)
            if tag in _VOID:
                return
            self.stack.append((tag, inside or attrs.get("id") == root_id))

        def handle_endtag(self, tag):
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    walk = _Walk()
    walk.feed(body)
    return walk.controls


def test_every_field_in_the_panel_belongs_to_the_form_that_saves_it(tmp_path):
    (tmp_path / "s").mkdir()
    settings = _client(tmp_path / "s", chosen=True).get("/personalize").text
    controls = _named_controls_inside(
        settings, "change-runs"
    )  # the panel and everything else that saves
    names = {c["name"] for c in controls}
    assert {
        "compute",
        "cloud_provider",
        "council_models",
        "provider_api_key",
        "escalation_provider",
        "escalation_api_key",
    } <= names
    assert all(c.get("form") == "compute-form" for c in controls), [
        c["name"] for c in controls if c.get("form") != "compute-form"
    ]
    # in setup the panel sits inside the one form, so its fields carry no form attribute
    (tmp_path / "t").mkdir()
    setup = _client(tmp_path / "t").get("/setup/model").text
    assert all("form" not in c for c in _named_controls_inside(setup, "cc-attach"))


def _contrast(fg: str, bg: str) -> float:
    def lum(h: str) -> float:
        r, g, b = (int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_number_and_optional_badges_have_readable_text_in_both_themes(tmp_path):
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "anthill/web/static/style.css").read_text()
    token = lambda name, block: re.search(rf"{name}:\s*(#[0-9A-Fa-f]{{6}})", block).group(1)  # noqa: E731
    light = css[css.index(":root") :]
    dark = css[css.index("@media (prefers-color-scheme: dark)") :]
    page = _client(tmp_path).get("/setup/model").text
    # light: white text on the accent, and on the darker warm text colour used as the badge fill
    assert _contrast("#FFFFFF", token("--accent", light)) >= 4.5
    assert _contrast("#FFFFFF", token("--warn-text", light)) >= 4.5
    assert "background:var(--warn-text,#8A5A12)" in page
    # dark: the page switches the text to dark on the lighter accent and amber
    assert _contrast("#08140D", token("--accent", dark)) >= 4.5
    assert _contrast("#08140D", token("--warn-text", dark)) >= 4.5
    for rule in (
        '[data-theme="dark"] .cc-num{color:#08140D}',
        '[data-theme="dark"] .cc-infer-badge{color:#08140D}',
    ):
        assert rule in page, rule


def test_each_privacy_line_names_what_it_is_about_so_the_compact_cards_can_drop_the_box(tmp_path):
    body = _client(tmp_path).get("/setup/model").text
    machine = re.search(r'<div class="cc-boundary-cap-shared">(.*?)</div>', body, re.S).group(1)
    assert '<span class="cc-subj">Model and knowledge base: </span>Stays on this device' in machine
    cloud = re.findall(r'<div class="cc-boundary-cap">(.*?)</div>', body, re.S)
    assert any(
        '<span class="cc-subj">Model: </span>Stays in your cloud account' in c for c in cloud
    )
    assert any(
        '<span class="cc-subj">Knowledge base: </span>Sent to your cloud when used' in c
        for c in cloud
    )
    # hidden in the full cards (the Model / Knowledge base box above the lines names them there), shown compact
    assert re.search(r"\.cc-subj\{display:none\}", body)
    assert re.search(r"@media\(max-width:900px\)\{[^@]*\.cc-subj\{display:inline", body, re.S)


def test_an_account_saved_on_your_cloud_posts_only_the_cloud_models_even_before_the_script(
    tmp_path,
):
    body = _client(tmp_path, chosen=True, compute="cloud").get("/personalize").text
    controls = _named_controls_inside(body, "cc-attach")
    cloud = [c for c in controls if "csel-cloud" in c.get("class", "")]
    local = [c for c in controls if "csel-local" in c.get("class", "")]
    assert cloud and not any("disabled" in c for c in cloud)
    assert local and all("disabled" in c for c in local)
    # and the other way round for an account saved on Your machine
    (tmp_path / "m").mkdir()
    body = _client(tmp_path / "m", chosen=True).get("/personalize").text
    controls = _named_controls_inside(body, "cc-attach")
    assert all("disabled" in c for c in controls if "csel-cloud" in c.get("class", ""))
    assert not any("disabled" in c for c in controls if "csel-local" in c.get("class", ""))


def test_the_vision_summary_is_set_from_the_checkbox_when_the_script_runs_and_when_the_page_is_shown(
    tmp_path,
):
    body = _client(tmp_path).get("/setup/model").text
    assert re.search(
        r"\nccVisionState\(\);\nwindow\.addEventListener\('pageshow', ccVisionState\);", body
    )


def test_an_installed_model_outside_the_catalog_is_disabled_with_the_machine_list(
    tmp_path, monkeypatch
):
    # The "installed, not in the curated list" rows are a second row template in the machine list
    # (_council_builder.html, other_installed): the server must disable them with the rest of that list
    # when the account is saved on Your cloud, and in Settings they belong to the form that saves them.
    import anthill.inference.ollama as ollama_mod

    tag = "my-own-model:1b"
    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": tag, "size_bytes": 1_000_000_000}],
    )
    for compute, disabled in (("cloud", True), ("local", False)):
        folder = tmp_path / compute
        folder.mkdir()
        body = _client(folder, chosen=True, compute=compute).get("/personalize").text
        assert "not in the curated list" in body
        rows = [c for c in _named_controls_inside(body, "cc-attach") if c.get("value") == tag]
        assert len(rows) == 1 and "csel-local" in rows[0]["class"], rows
        assert ("disabled" in rows[0]) is disabled, compute
        assert rows[0].get("form") == "compute-form"  # and it saves with the Settings form


def test_the_grey_note_about_storage_is_gone_from_both_screens(tmp_path):
    # It sat under the cards next to "Knowledge base: Sent to your cloud when used" and read as the opposite
    # (founder, 2026-10-12). The cards' own privacy lines are the one statement about the knowledge base.
    for name, url in (("setup", "/setup/model"), ("settings", "/personalize")):
        folder = tmp_path / name
        folder.mkdir()
        body = _client(folder, chosen=name == "settings").get(url).text
        assert "stored on this device either way" not in body, name
        assert "cc-knowledge-note" not in body, name
        assert "Sent to your cloud when used" in body, name
