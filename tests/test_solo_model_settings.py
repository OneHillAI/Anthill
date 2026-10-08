"""The Solo / this-device model is a USER setting (Personalize), chosen from a dropdown - not an
org Settings free-text field. Saving org Settings must not reset it."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_personalize_has_a_council_builder(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    # Settings mirrors the setup chooser: a council builder (posts name="council_models") behind
    # "Change where it runs", not a single-model dropdown.
    assert 'name="council_models"' in body and "Choose your council" in body
    assert "this device" in body.lower()  # framed as a you/this-device setting


def test_personalize_does_not_repeat_the_where_it_runs_heading(tmp_path, monkeypatch):
    # Founder report, 2026-09-28: "Where your AI runs" (the summary card) immediately followed by
    # "Where it runs" / "Where does your model run?" (the expanded chooser) read as redundant,
    # LLM-typical repetition. The expanded chooser's own heading + tooltip only belong in the setup
    # wizard, where there is no summary above it yet; personalize.html carries the tooltip instead.
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Where your AI runs" in body  # the one summary heading
    assert "Where does your model run?" not in body  # not repeated inside "Change where it runs"
    assert "Step 1" not in body  # the wizard's own step numbering doesn't belong in Settings
    # the tier cards themselves are still there - only the redundant heading text is gone
    assert "Your machine" in body and "Your cloud" in body
    # the explanation moved onto the summary heading instead of being dropped
    assert "Your machine keeps everything on this device" in body


def test_personalize_saves_the_local_model(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize", data={"ollama_model": "qwen2.5:14b", "profile": "x"}, follow_redirects=False
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen2.5:14b"


def test_settings_no_longer_has_the_local_model_field(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/settings").text
    assert 'name="ollama_model"' not in body  # moved to Personalize
    assert "Local inference" in body and "/personalize" in body


def test_settings_save_does_not_clobber_the_local_model(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize", data={"ollama_model": "qwen2.5:14b", "profile": "x"}, follow_redirects=False
    )
    # saving org Settings (no ollama_model field) must leave the personal local model intact
    c.post(
        "/settings",
        data={"ollama_url": "http://x:11434", "cache_threshold": "0.9"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen2.5:14b"


# ── Solo settings home: the compute choice lives here now (P1) ──────────────────


def test_personalize_is_the_settings_home_with_compute(tmp_path, monkeypatch):
    # Settings leads with a "Where your AI runs" summary; the compute picker (Model & compute) still lives
    # here behind "Change where it runs", so the real solo_compute choice is preserved (not the setup
    # picker as the default view).
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Where your AI runs" in body  # the summary card leads
    assert 'onclick="toggleChangeRuns()"' in body  # ...with a "Change where it runs" action
    # Behind it: the same 2-tier ownership chooser as first-run setup, posting to /personalize/compute.
    assert 'id="cc-tierseg"' in body and 'action="/personalize/compute"' in body


def test_personalize_saves_the_solo_compute_choice(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize",
        data={"solo_compute": "cloud", "profile": "x"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.solo_compute == "cloud"
    # an invalid value is ignored (routing only ever sees local|cloud)
    c.post("/personalize", data={"solo_compute": "bogus", "profile": "x"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.solo_compute == "cloud"  # unchanged; not coerced to a bogus value


def test_personalize_compute_cards_are_the_two_ownership_tiers_with_attachment(
    tmp_path, monkeypatch
):
    # The compute choice (behind "Change where it runs") is the 2-tier ownership chooser: Your machine
    # (Full) / Your cloud (Full, self-provisioned), each with an optional inference-provider attachment
    # for hard questions - not a third, competing "Partial ownership" tier.
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Your machine" in body and "Your cloud" in body
    # the "what you get" facts moved from a standing bullet list into the "?" popover (Wave 2:
    # docs/specs/helper-text-wave2.md - no standing explanatory prose on working surfaces).
    assert "you fully own" in body.lower()
    assert "Your inference provider" in body  # the always-visible attachment card
    assert "ownershipindex.ai" in body  # inference providers link to their AOI grade


def test_settings_privacy_toggles_persist(tmp_path, monkeypatch):
    # Privacy has REAL toggles (not an empty text card): "Improve my model" -> auto_memory_off, and
    # "Scrub personal details" -> cloud_scrub_pii. Unchecking a box (absent from the form) turns it off.
    from anthill.web.db import User

    c, app_mod = _client(tmp_path, monkeypatch)
    # both on
    c.post("/personalize", data={"memory_on": "on", "scrub_pii": "on"}, follow_redirects=False)
    s = app_mod._SessionFactory()
    assert s.query(User).first().auto_memory_off is False  # memory on
    assert s.query(OrgSettings).first().cloud_scrub_pii is True
    # save with both unchecked (absent) -> both off
    c.post("/personalize", data={}, follow_redirects=False)
    s = app_mod._SessionFactory()
    assert s.query(User).first().auto_memory_off is True  # memory turned off
    assert s.query(OrgSettings).first().cloud_scrub_pii is False


def test_settings_web_access_toggle_persists(tmp_path, monkeypatch):
    # "Web access" is a real Privacy toggle now (not "coming soon"): it persists to User.web_access_on,
    # on for a new account, and seeds each chat's Web-search toggle (a chat can change its own).
    from anthill.web.db import User

    c, app_mod = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Web access" in body and 'name="web_access"' in body  # a real toggle, not "coming soon"
    c.post("/personalize", data={"web_access": "on"}, follow_redirects=False)
    assert app_mod._SessionFactory().query(User).first().web_access_on is True
    c.post("/personalize", data={}, follow_redirects=False)  # unchecked -> off
    assert app_mod._SessionFactory().query(User).first().web_access_on is False


def test_settings_intelligence_has_persona_and_no_personal_knowledge_bar(tmp_path, monkeypatch):
    # Model/Knowledge/Personality are split into their own tabs now (one workflow each) rather than one
    # "Intelligence" grab-bag, but all render in the same page (CSS toggles which tab shows) - so a
    # persona editor ("How your AI sounds"), a council card, and no old profile-links bar all still show
    # up in the body regardless of tab.
    from anthill.hosting import sizing

    # "Your council" only renders when there's something council-specific to show (2026-09-29 fix -
    # a single model with no council fit already named its model in "Where your AI runs" above, so a
    # second card just repeating that was pure noise). Mock hardware that fits a council so this
    # incidental assertion stays meaningful and independent of the real test-runner's specs.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (48.0, "gpu"))
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert (
        "How your AI sounds" in body and 'name="profile"' in body
    )  # the persona editor (Personality)
    assert "Your council" in body  # the council card (Model tab; self-tuning is gated on tune)
    assert 'href="/snippets"' not in body  # the confusing personal-knowledge link bar is gone


def test_settings_shows_and_manages_a_solo_council(tmp_path, monkeypatch):
    # Model -> Your council shows a real multi-model council when one is configured, and can revert
    # to a single model. (Set-up uses hardware-fit suggestions; here we seed a council directly.)
    import json

    c, app_mod = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(OrgSettings).first().org_council_members = json.dumps(
        [{"model": "qwen2.5:7b"}, {"model": "llama3.1:8b"}]
    )
    s.commit()
    body = c.get("/personalize").text
    assert ">2 models<" in body  # the council pill counts the members
    assert "qwen2.5:7b" in body and "llama3.1:8b" in body  # both members are shown
    assert "Use one model" in body  # the revert action (a council is active)
    # reverting clears the council (keeping the lead as the single model)
    r = c.post("/personalize/council", data={"action": "single"})
    assert r.json()["ok"] is True
    assert (
        json.loads(app_mod._SessionFactory().query(OrgSettings).first().org_council_members) == []
    )


def test_your_council_card_hidden_when_theres_nothing_council_specific_to_show(
    tmp_path, monkeypatch
):
    # Founder report, 2026-09-29: a single model on a machine too small for a council restated the
    # exact model already named in full in "Where your AI runs" above - a second card saying the
    # same thing a different way. It should only render when there's an active multi-model council,
    # or hardware that could actually fit one.
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # under the 24GB floor
    c, _ = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize", data={"ollama_model": "qwen2.5:7b", "profile": "x"}, follow_redirects=False
    )
    body = c.get("/personalize").text
    assert "<h3>Your council" not in body
    assert "qwen2.5:7b" in body  # still named once, in "Where your AI runs"


def test_settings_appearance_is_a_real_light_dark_control(tmp_path, monkeypatch):
    # Appearance is a real Light / Dark / System control now (not "coming soon"), wired to the app-wide
    # pre-paint theme script in base.html.
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'id="appearance-seg"' in body  # the segmented control
    for opt in (">Light<", ">Dark<", ">System<"):
        assert opt in body, opt
    assert "anthillPickTheme" in body  # the control handler
    assert "anthillSetTheme" in body  # the app-wide theme setter (base.html head, no-flash)


def test_council_setup_route_is_graceful(tmp_path, monkeypatch):
    # Setting up a council never 500s - it returns ok true/false depending on whether a fitting council is
    # found for this machine (suggest_local_setup), and an unknown action is rejected cleanly.
    c, _ = _client(tmp_path, monkeypatch)
    r = c.post("/personalize/council", data={"action": "council"})
    assert r.status_code == 200 and "ok" in r.json()
    assert c.post("/personalize/council", data={"action": "bogus"}).status_code == 400


def test_solo_settings_is_in_the_nav(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/chat").text
    assert (
        ">Settings</a>" in body
    )  # the single Settings entry points at the personal home (/personalize)
    assert 'href="/personalize"' in body


def test_settings_page_has_scope_tabs(tmp_path, monkeypatch):
    # The Settings page is organised into one-workflow-per-tab scope tabs: Model / Knowledge /
    # Personality / This device / Privacy, plus an admin-only Organisation tab. Model (where it runs,
    # the model/provider decision) is the default active tab, not This device (appearance/storage only).
    c, _ = _client(tmp_path, monkeypatch)  # admin
    body = c.get("/personalize").text
    for tab in (
        ">Model<",
        ">Knowledge<",
        ">Personality<",
        ">This device<",
        ">Privacy<",
        ">Organisation<",
    ):
        assert tab in body, tab
    assert 'data-st="model"' in body and 'class="st-panel active" data-st="model"' in body
