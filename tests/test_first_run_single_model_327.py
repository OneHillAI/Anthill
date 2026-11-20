"""First run on a small machine suggests ONE model, never a council below 64 GB (#327)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import sizing
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _pick(family, params_b, tag):
    m = sizing.Model(
        name=f"{family} {tag}",
        params_b=params_b,
        ollama_tag=tag,
        family=family,
        origin="Test, X",
        active_b=params_b,
    )
    return sizing.FamilyPick(family=family, origin="Test, X", recommended=m, download_gb=params_b)


@pytest.fixture
def three_that_fit(monkeypatch):
    picks = [_pick("a", 3, "a:3b"), _pick("b", 2, "b:2b"), _pick("c", 1, "c:1b")]
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: picks)
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: True)
    monkeypatch.setattr(sizing, "onprem_council_fits_on_disk", lambda params: True)
    return picks


@pytest.mark.parametrize("mem_gb", [8, 16, 24, 32, 48, 63.9])
def test_below_64_gb_the_suggestion_is_one_model_even_when_three_would_fit(three_that_fit, mem_gb):
    s = sizing.suggest_local_setup(mem_gb, "apple")
    assert s.mode == "single" and [p.family for p in s.members] == [
        "a"
    ]  # the strongest family's pick


@pytest.mark.parametrize("mem_gb", [64, 128])
def test_from_64_gb_a_council_is_still_offered_where_memory_and_disk_allow(three_that_fit, mem_gb):
    s = sizing.suggest_local_setup(mem_gb, "apple")
    assert s.mode == "council" and len(s.members) == 3


def test_from_64_gb_a_single_model_when_the_three_do_not_fit(three_that_fit, monkeypatch):
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: False)
    assert sizing.suggest_local_setup(64, "apple").mode == "single"
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: True)
    monkeypatch.setattr(sizing, "onprem_council_fits_on_disk", lambda params: False)
    assert sizing.suggest_local_setup(64, "apple").mode == "single"


def test_the_threshold_is_a_named_constant_at_64_gb():
    assert sizing.FIRST_RUN_COUNCIL_MIN_GB == 64.0


def test_with_nothing_that_fits_there_are_no_members(monkeypatch):
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: [])
    s = sizing.suggest_local_setup(8, "apple")
    assert s.mode == "single" and s.members == []


def test_the_shipped_catalogue_gives_one_model_at_every_small_tier(monkeypatch):
    monkeypatch.setattr(
        sizing, "onprem_council_fits_on_disk", lambda params: True
    )  # plenty of disk
    for mem_gb in (8, 16, 24, 32, 48):
        s = sizing.suggest_local_setup(mem_gb, "apple")
        assert s.mode == "single" and len(s.members) <= 1


# ── the setup form provisions exactly what the suggestion says ─────────────────────────────────────────────


def _client(tmp_path):
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
    s.add(OrgSettings(org_id=o.id, deployment_topology="solo", local_model_chosen=False))
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_accepting_the_first_run_suggestion_on_an_8_gb_mac_starts_one_download(
    tmp_path, monkeypatch, three_that_fit
):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (8.0, "apple"))
    monkeypatch.setattr(sizing, "LOCAL_CATALOG", (three_that_fit[0].recommended,), raising=False)
    c, app_mod = _client(tmp_path)
    pulled = []
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.append(tag))
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [])
    r = c.post(
        "/setup/model", data={"compute": "local", "accept_suggestion": "1"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert pulled == ["a:3b"]  # one small model, not three
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_council_members == "[]" and cfg.local_model_chosen is True
    assert cfg.ollama_model == "a:3b"
