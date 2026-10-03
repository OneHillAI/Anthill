"""The Settings compute picker (personalize -> This device -> Change where it runs) is a guided,
fit-aware model list, not a bare tag dropdown: each model shows whether it fits THIS machine (and why),
a capability signal, and its origin; an origin filter lets a user scope by sovereignty region. Selection
still round-trips through name="ollama_model" so the server-side fit guard applies uniformly.
"""

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
    o = Organization(name="Solo", slug="s")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id, deployment_topology="solo", ollama_model="qwen2.5:7b")])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_picker_shows_fit_capability_and_origin(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'class="mp-list"' in body  # the council builder list, not a bare <select>
    assert 'name="council_models"' in body  # Settings mirrors the setup council builder
    assert 'class="fit' in body  # a fit verdict per model (Fits / tight / slower)
    assert "Model origin:" in body  # the origin/sovereignty filter


def test_picker_has_a_sovereignty_origin_filter(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Model origin:" in body
    for region in (">US<", ">EU<", ">China<", ">Other<"):
        assert region in body, region
    assert "mpFilter" in body  # the client-side filter handler
    assert "data-region=" in body  # rows carry a region for the filter to match


def test_uninstall_button_next_to_installed_badge_hides_for_the_active_model(tmp_path, monkeypatch):
    # Founder ask, 2026-09-30: uninstall right where you're already looking at your models, not a
    # separate Settings > This device > Manage page. Never offered for a currently-active tag (lead
    # or any council member) - deleting your own running model would fail server-side and make no
    # sense mid-selection.
    import anthill.inference.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [
            {"name": "qwen3.5:4b", "size_bytes": 3_200_000_000},
            {"name": "nemotron-3-nano:4b", "size_bytes": 2_600_000_000},
        ],
    )
    c, _ = _client(tmp_path, monkeypatch)  # ollama_model="qwen2.5:7b" is NOT in this installed list
    # Reconfigure the current model to one of the two "installed" tags, so it's the row under test.
    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({"ollama_model": "qwen3.5:4b"})
    s.commit()

    body = c.get("/personalize").text
    assert "mcUninstall(this, 'qwen3.5:4b')" not in body  # the active/lead model: no uninstall
    assert (
        "mcUninstall(this, 'nemotron-3-nano:4b')" in body
    )  # installed, not active: uninstall offered


def test_uninstall_button_is_never_shown_during_first_run_setup(tmp_path, monkeypatch):
    import anthill.inference.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": "nemotron-3-nano:4b", "size_bytes": 2_600_000_000}],
    )
    c, app_mod = _client(tmp_path, monkeypatch)
    from anthill.web.db import OrgSettings

    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({"local_model_chosen": False})
    s.commit()
    body = c.get("/setup/model").text
    assert "installed" in body  # the badge itself still shows
    # the mcUninstall() JS function is always defined (shared script), but no row should call it
    assert "mcUninstall(this," not in body  # no uninstall control during setup


def test_non_catalog_installed_model_is_a_real_selectable_row(tmp_path, monkeypatch):
    # Founder pushback, 2026-09-30: "these are core models [Qwen, Mistral, Llama, Granite]... why
    # should they not be in the normal list?" mistral:7b (an older, no-longer-curated Mistral release
    # - the catalog has "Mistral Small 3 24B" instead) fits this machine trivially but isn't a
    # catalog entry, so it used to be invisible everywhere in this picker. It's not second-class:
    # real checkbox, same council_models mechanism as any catalog row - _apply_solo_compute already
    # accepts an arbitrary tag for a single-model selection with no catalog-membership check at all.
    import anthill.inference.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": "mistral:7b", "size_bytes": 4_400_000_000}],
    )
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'value="mistral:7b"' in body  # a real checkbox value, not just a badge
    assert 'name="council_models"' in body.split('value="mistral:7b"')[0][-200:]  # same input
    assert "mcUninstall(this, 'mistral:7b')" in body  # still removable, like any installed model
    assert "not in the curated list" in body  # labelled honestly, not passed off as a catalog pick


def test_non_catalog_installed_model_is_selectable_during_first_run_setup(tmp_path, monkeypatch):
    # Bug caught live, 2026-10-01: the row's own `{% for m in other_installed if not in_setup %}`
    # loop guard hid the ENTIRE row during setup, not just its Uninstall button - contradicting the
    # same founder principle above ("why should they not be in the normal list?"). A fresh account
    # whose machine already has Ollama models pulled (verified live: 9 of 13 installed tags on a real
    # test machine) must be able to pick one of them before ever finishing setup, same as afterward in
    # Settings - only the Uninstall control has any reason to differ by in_setup, not the row itself.
    import anthill.inference.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": "mistral:7b", "size_bytes": 4_400_000_000}],
    )
    c, app_mod = _client(tmp_path, monkeypatch)
    from anthill.web.db import OrgSettings

    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({"local_model_chosen": False})
    s.commit()
    body = c.get("/setup/model").text
    assert 'value="mistral:7b"' in body  # real checkbox row, not hidden during setup
    assert "not in the curated list" in body
    assert "mcUninstall(this," not in body  # uninstall itself still absent during setup


def test_selecting_a_non_catalog_installed_model_as_lead_saves_with_no_catalog_check(
    tmp_path, monkeypatch
):
    import anthill.inference.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": "mistral:7b", "size_bytes": 4_400_000_000}],
    )
    c, app_mod = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)
    from anthill.web.db import OrgSettings

    r = c.post(
        "/personalize/compute",
        data={"compute": "local", "council_models": ["mistral:7b"], "council_lead": "mistral:7b"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "mistral:7b"


def test_origin_region_bucketing():
    from anthill.web.app import _origin_region

    assert _origin_region("Meta, US") == "us"
    assert _origin_region("OpenAI, US") == "us"
    assert _origin_region("Alibaba, China") == "china"
    assert _origin_region("DeepSeek, China") == "china"
    assert _origin_region("Mistral, France") == "eu"
    assert _origin_region("Some Lab, Canada") == "other"
    assert _origin_region("") == "other"
    # a country merely CONTAINING "us" (Belarus, Cyprus) must not be mislabelled US
    assert _origin_region("Foo, Belarus") != "us"


def test_picker_only_lets_you_select_a_fitting_model(tmp_path, monkeypatch):
    # Fit is enforced in the UI: an out-of-fit model's radio is disabled, so a user cannot pick a model
    # that won't run on their machine (the exact bug: gpt-oss 120B "selected" on a 16GB box).
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    # if any model is too big for this machine, its row is a disabled radio inside a .nofit row
    assert 'class="mp-row nofit"' in body or "disabled" in body


def test_machine_and_cloud_have_separate_toggled_sections(tmp_path, monkeypatch):
    # Gripe #1: choosing Your machine vs Your cloud must change what's shown. Both tier cards are
    # always visible (id="cc-tierseg" wraps both - a click just selects one, matching the founder-
    # approved mockup instead of a segmented switch that hides the other); picking "Your cloud"
    # reveals its own required cloud-provider connect area (id="cc-connect-cloud").
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'id="cc-tierseg"' in body  # wraps both always-visible tier cards
    assert "ccPickTier" in body  # selecting a tier reveals its provider connect
    assert 'id="cc-connect-cloud"' in body  # the cloud tier's own required provider-connect area


def test_cloud_reveals_bigger_models_and_providers(tmp_path, monkeypatch):
    # Gripe #5: the cloud side must show the bigger models cloud unlocks + the providers, not nothing.
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "RunPod" in body and "Lambda" in body  # self-provisioned cloud providers
    assert "Berget" in body and "Groq" in body and "Infercom" in body  # inference providers
    assert "Advanced setups" in body  # Server-URL / own-cloud live here now


def test_picker_selection_round_trips_through_personalize_post(tmp_path, monkeypatch):
    # The chooser posts name="ollama_model" to /personalize, same as before, so Dev Council's server-side
    # fit guard applies regardless of what the GET rendered.
    c, app_mod = _client(tmp_path, monkeypatch)
    c.post(
        "/personalize", data={"ollama_model": "qwen2.5:7b", "profile": "x"}, follow_redirects=False
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen2.5:7b"
