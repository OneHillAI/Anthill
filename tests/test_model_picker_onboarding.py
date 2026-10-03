"""Option-A first-run model picker: a solo install picks a family + size BEFORE anything downloads.
The dashboard gates an unchosen solo install to /setup/model; choosing records the model + starts the
pull; skipping defers without downloading. No real Ollama is touched (installed_models() returns [] when
the server is unreachable, and the pull is stubbed)."""

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, *, topology="solo", chosen=False):
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
    s.add(OrgSettings(org_id=o.id, deployment_topology=topology, local_model_chosen=chosen))
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_picker_lists_all_four_families(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/setup/model").text
    assert "Pick your artificial intelligence" in body
    for fam in ("Llama", "Gemma", "Mistral", "Qwen"):
        assert fam in body
    assert "Your machine" in body  # the 3-tier ownership chooser
    assert 'name="choice"' in body  # radio choices present, inside the Advanced/manual section


def test_choosing_a_model_records_it_and_starts_the_pull(tmp_path, monkeypatch):
    import anthill.hosting.sizing as sizing

    # This test is about the record/pull behavior, not the fit guard - pin a machine the chosen
    # 9B model comfortably fits on so it behaves the same on any real host running the suite
    # (a CI runner's actual detected hardware is much smaller than a dev machine's).
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    c, app_mod = _client(tmp_path)
    pulled = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.update(tag=tag))
    r = c.post("/setup/model", data={"choice": "qwen3.5:9b"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen3.5:9b" and cfg.local_model_chosen is True
    assert pulled["tag"] == "qwen3.5:9b"  # download started for the chosen model


def test_skipping_defers_without_downloading(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path)
    pulled = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.update(tag=tag))
    r = c.post("/setup/model", data={"choice": "__skip__"}, follow_redirects=False)
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.local_model_chosen is True  # won't be re-prompted
    assert cfg.ollama_model == "qwen2.5:3b"  # unchanged default
    assert pulled == {}  # nothing pulled


def test_arbitrary_tag_is_not_pulled(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path)
    pulled = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.update(tag=tag))
    c.post("/setup/model", data={"choice": "evil/not-a-catalog-model"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen2.5:3b" and pulled == {}  # ignored, never pulled
    assert cfg.local_model_chosen is True  # still recorded so we don't loop forever


# ── vision auto-download (task_54ced0fe): default-on, opt-out, best-effort ──────


def test_vision_autopull_wanted_gating():
    import types

    from anthill.web.app import _vision_autopull_wanted

    on = types.SimpleNamespace(vision_autopull=True)
    off = types.SimpleNamespace(vision_autopull=False)
    assert _vision_autopull_wanted(on, set()) is True  # toggle on + no vision model -> pull
    assert _vision_autopull_wanted(on, {"qwen3:8b"}) is True  # a text model doesn't count
    # The default vision model is now the licence-clean non-Chinese Granite; its family counts.
    assert (
        _vision_autopull_wanted(on, {"granite3.2-vision:2b"}) is False
    )  # already have the default
    # A different-family vision model (even a valid one) still triggers the clean-default pull.
    assert _vision_autopull_wanted(on, {"qwen2.5vl:7b"}) is True
    assert _vision_autopull_wanted(off, set()) is False  # toggle off -> never
    assert _vision_autopull_wanted(None, set()) is False  # no cfg -> never


def test_choosing_a_model_with_vision_enabled_persists_and_triggers(tmp_path, monkeypatch):
    import anthill.hosting.sizing as sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # see comment above
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    vision = {}
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: vision.update(called=oid))
    r = c.post(
        "/setup/model",
        data={"choice": "qwen3.5:9b", "enable_vision": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.vision_autopull is True  # opt-in persisted
    assert vision.get("called") == cfg.org_id  # the vision autopull was triggered


def test_unchecking_vision_disables_autopull(tmp_path, monkeypatch):
    import anthill.hosting.sizing as sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # see comment above
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)
    # enable_vision absent (checkbox unticked) -> autopull disabled
    c.post("/setup/model", data={"choice": "qwen3.5:9b"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.vision_autopull is False


def test_maybe_autopull_vision_is_a_noop_when_toggle_off(tmp_path, monkeypatch):
    _c, app_mod = _client(tmp_path)
    s = app_mod._SessionFactory()
    row = s.query(OrgSettings).first()
    row.vision_autopull = False
    org_id = row.org_id
    s.commit()
    started = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: started.update(tag=tag))
    app_mod._maybe_autopull_vision(org_id)  # returns before spawning any thread
    assert started == {}  # toggle off -> nothing pulled


def test_picker_shows_the_vision_toggle(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/setup/model").text
    assert 'name="enable_vision"' in body and "Enable image understanding" in body


def test_dashboard_gates_unchosen_solo_to_the_picker(tmp_path):
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/setup/model"


def test_dashboard_does_not_gate_after_choosing(tmp_path):
    c, _ = _client(tmp_path, topology="solo", chosen=True)
    r = c.get("/", follow_redirects=False)
    assert r.headers.get("location") != "/setup/model"  # proceeds to the dashboard


def test_dashboard_does_not_gate_an_org_install(tmp_path):
    c, _ = _client(tmp_path, topology="org", chosen=False)
    r = c.get("/", follow_redirects=False)
    assert r.headers.get("location") != "/setup/model"  # the picker is a solo flow


# ── server-side fit guard: complete the pattern #747 added to /personalize and /models/pull ────────
# (the picker greys out a too-large option, but nothing re-checked a submitted `choice` here - a
# stale page, a not-fully-disabled option, or a direct request could still select and download it)


def _big_catalog_tag():
    from anthill.hosting.sizing import load_catalog

    return next(m.ollama_tag for m in load_catalog() if m.ollama_tag and m.params_b >= 70)


def test_choosing_a_model_too_large_for_this_machine_is_rejected(tmp_path, monkeypatch):
    import anthill.hosting.sizing as sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [])
    pulled = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.update(tag=tag))
    big = _big_catalog_tag()
    r = c.post("/setup/model", data={"choice": big}, follow_redirects=False)
    assert r.status_code == 302 and "error=too_large" in r.headers["location"]
    assert pulled == {}  # no download kicked off
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model != big and cfg.local_model_chosen is False  # unchanged, not marked done


def test_an_already_installed_too_large_model_still_works(tmp_path, monkeypatch):
    # _model_too_large_for_this_machine's own fail-open rule: already on disk -> no new risk.
    import anthill.hosting.sizing as sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    c, app_mod = _client(tmp_path)
    big = _big_catalog_tag()
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [big])
    r = c.post("/setup/model", data={"choice": big}, follow_redirects=False)
    assert r.status_code == 302 and "error=too_large" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert (
        cfg.ollama_model == big
    )  # activated immediately, matching the pre-existing installed path


def test_too_large_error_banner_renders_on_the_picker(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/setup/model?error=too_large").text
    assert "too large" in body.lower()


def _fake_get_returning_unparseable_json(*_a, **_k):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")  # e.g. an HTML error page on a 200

    return _Resp()


def test_maybe_autopull_vision_survives_a_malformed_ollama_response(tmp_path, monkeypatch):
    _c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    row = app_mod._SessionFactory().query(OrgSettings).first()
    org_id, row.vision_autopull = row.org_id, True
    app_mod._SessionFactory().commit()
    monkeypatch.setattr(app_mod.httpx, "get", _fake_get_returning_unparseable_json)
    before = set(threading.enumerate())
    app_mod._maybe_autopull_vision(org_id)  # must not raise
    # It starts a background thread that calls _start_model_pull. Let it finish while the stub above is
    # still in place: a thread that wakes after the patch is undone starts a real `ollama pull`.
    for t in set(threading.enumerate()) - before:
        t.join(timeout=5)


def test_choosing_a_model_survives_a_malformed_ollama_response(tmp_path, monkeypatch):
    # The synchronous probe in model_picker_post itself must not 500 the request - it now goes
    # through _installed_local_models (already exception-safe) instead of a raw OllamaBackend call.
    c, app_mod = _client(tmp_path)
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    monkeypatch.setattr(app_mod.httpx, "get", _fake_get_returning_unparseable_json)
    r = c.post("/setup/model", data={"choice": "qwen3.5:9b"}, follow_redirects=False)
    assert r.status_code == 302  # not a 500
