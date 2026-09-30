"""The server-side half of the model-fit check.

_local_model_options/_model_picker_view already show fit annotations in the UI (models.html,
personalize.html), but neither personalize_post nor /models/pull re-validated the submitted tag - so
a stale saved choice, a not-fully-disabled option, or a direct request could still select and
download a model far too large for this machine (e.g. a 120B-param model on 16GB Apple Silicon
unified memory, which has no separate VRAM to spill the rest to). This covers the guard that closes
that gap: _model_too_large_for_this_machine, and its use in both endpoints.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setattr("anthill.inference.ollama.find_ollama_bin", lambda: None)
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [User(org_id=o.id, email="a@a.com", role="admin", active=True), OrgSettings(org_id=o.id)]
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def _big_and_small_tags():
    from anthill.hosting.sizing import load_catalog

    cat = load_catalog()
    big = next(m.ollama_tag for m in cat if m.ollama_tag and m.params_b >= 70)
    small = next(m.ollama_tag for m in cat if m.ollama_tag and 0 < m.params_b <= 8)
    return big, small


def _apple_16gb(monkeypatch):
    import anthill.hosting.sizing as sizing
    import anthill.web.app as app_mod

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [])


# ── _model_too_large_for_this_machine ────────────────────────────────────────────────────────────


def test_a_catalog_model_too_big_for_this_machine_is_flagged(monkeypatch):
    import types

    import anthill.web.app as app_mod

    _apple_16gb(monkeypatch)
    big, _small = _big_and_small_tags()
    cfg = types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    assert app_mod._model_too_large_for_this_machine(cfg, big) is True


def test_a_catalog_model_that_fits_is_not_flagged(monkeypatch):
    import types

    import anthill.web.app as app_mod

    _apple_16gb(monkeypatch)
    _big, small = _big_and_small_tags()
    cfg = types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    assert app_mod._model_too_large_for_this_machine(cfg, small) is False


def test_an_already_installed_too_big_model_is_not_flagged(monkeypatch):
    # Already on disk - no new download/switch risk, and blocking a reference to it would be a
    # usability regression (e.g. someone who set it up manually before this guard existed).
    import types

    import anthill.hosting.sizing as sizing
    import anthill.web.app as app_mod

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    big, _small = _big_and_small_tags()
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [big])
    cfg = types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    assert app_mod._model_too_large_for_this_machine(cfg, big) is False


def test_an_unrecognized_custom_tag_is_not_flagged(monkeypatch):
    # No params_b to judge a tag Anthill's catalog doesn't know - fail open, matching
    # _local_model_options' own "power-user free-text still downloads" behaviour.
    import types

    import anthill.web.app as app_mod

    _apple_16gb(monkeypatch)
    cfg = types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    assert app_mod._model_too_large_for_this_machine(cfg, "hf.co/bartowski/Some-GGUF") is False


# ── /models/pull ──────────────────────────────────────────────────────────────────────────────────


def test_pull_refuses_a_model_too_large_for_this_machine(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _apple_16gb(monkeypatch)
    big, _small = _big_and_small_tags()
    started = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: started.update(t=tag))
    r = c.post("/models/pull", data={"model_tag": big}, follow_redirects=False)
    assert "error=too_large" in r.headers["location"]
    assert started == {}  # no download kicked off
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model != big


def test_pull_still_allows_a_fitting_model(tmp_path, monkeypatch):
    c, app_mod, _org_id = _admin(tmp_path, monkeypatch)
    _apple_16gb(monkeypatch)
    _big, small = _big_and_small_tags()
    started = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: started.update(t=tag))
    r = c.post("/models/pull", data={"model_tag": small}, follow_redirects=False)
    assert "pulling=" in r.headers["location"]
    assert started == {"t": small}


# ── /personalize ──────────────────────────────────────────────────────────────────────────────────


def test_personalize_refuses_switching_to_a_model_too_large_for_this_machine(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _apple_16gb(monkeypatch)
    big, small = _big_and_small_tags()
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().ollama_model = small
    s.commit()
    r = c.post("/personalize", data={"ollama_model": big}, follow_redirects=False)
    assert "error=model_too_large" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model == small  # unchanged


def test_personalize_allows_switching_away_from_a_too_large_model(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _apple_16gb(monkeypatch)
    big, small = _big_and_small_tags()
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().ollama_model = big
    s.commit()
    r = c.post("/personalize", data={"ollama_model": small}, follow_redirects=False)
    assert "error=model_too_large" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model == small


def test_personalize_resubmitting_the_same_stale_too_large_value_does_not_lock_out_other_saves(
    tmp_path, monkeypatch
):
    # The dropdown always includes the CURRENT selection (_local_model_options), so an unrelated
    # Settings save (e.g. editing a profile field) resubmits the same value unchanged. A guard that
    # re-blocks an unchanged value would lock a user who already has a too-large model out of saving
    # anything else in this form until they fix the model choice first - a real regression this test
    # guards against.
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _apple_16gb(monkeypatch)
    big, _small = _big_and_small_tags()
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().ollama_model = big
    s.commit()
    r = c.post(
        "/personalize", data={"ollama_model": big, "role": "Engineer"}, follow_redirects=False
    )
    assert "error=model_too_large" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model == big  # unchanged, still saved without being blocked
