"""A catalogue row can say which engine it needs; the local picker hides rows the bundled engine cannot run (#322).

`min_engine_version` is the oldest Ollama that can download and run the row. The app compares it with the engine it
ships (the pin in anthill/inference/ollama.py), without touching the network, and does not offer a row that needs a newer
one. A row without the field, or with a value that is not a version, is offered as before. No shipped row has the field
yet, so every current verdict must stay the same. No network, no engine.
"""

import json

import pytest

from anthill.hosting import sizing
from anthill.hosting.sizing import Model

TIERS_GB = (8, 16, 24, 32, 64)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.40.16", "0.40.16"),
        ("v0.40.16", "0.40.16"),
        (" 0.40 ", "0.40.0"),
        ("1.2.3", "1.2.3"),
        ("0.30.10", "0.30.10"),
    ],
)
def test_a_version_is_read(value, expected):
    assert sizing._valid_engine_version(value) == expected


@pytest.mark.parametrize(
    "value", [None, "", "latest", "0.40.x", "1.2.3.4", "0", 40, 0.4, True, [], {}]
)
def test_anything_that_is_not_a_version_means_no_requirement(value):
    assert sizing._valid_engine_version(value) == ""


def test_a_row_carries_its_requirement_through_the_loader():
    row = {"name": "A", "ollama_tag": "a:4b", "params_b": 4, "min_engine_version": "0.40.16"}
    assert sizing._model_from_row(row).min_engine_version == "0.40.16"
    assert sizing._model_from_row({**row, "min_engine_version": "soon"}).min_engine_version == ""


def _row(name, family, intelligence, params=4.0, **kw):
    return Model(
        name=name,
        params_b=params,
        ollama_tag=f"{name.lower()}:x",
        family=family,
        intelligence=intelligence,
        active_b=params,
        **kw,
    )


@pytest.fixture
def shipping_0_30_10(monkeypatch):
    monkeypatch.setattr(sizing, "bundled_engine_version", lambda: (0, 30, 10))


def test_only_a_row_that_names_a_newer_engine_is_hidden(shipping_0_30_10):
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1)) is True
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1, min_engine_version="0.30.10")) is True
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1, min_engine_version="0.30.9")) is True
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1, min_engine_version="0.30.11")) is False
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1, min_engine_version="0.40.16")) is False


def test_an_unreadable_shipped_version_hides_nothing(monkeypatch):
    monkeypatch.setattr(sizing, "bundled_engine_version", lambda: None)
    assert sizing.runs_on_bundled_engine(_row("a", "F", 1, min_engine_version="99.0.0")) is True


def test_the_shipped_engine_version_is_the_pin():
    from anthill.inference.ollama import _OLLAMA_VERSION

    assert sizing.bundled_engine_version() == sizing._engine_version_tuple(_OLLAMA_VERSION)
    assert sizing.bundled_engine_version() is not None


def test_a_hidden_row_is_never_picked_or_suggested(shipping_0_30_10, monkeypatch):
    monkeypatch.setattr(sizing, "is_usable_speed", lambda m, kind: True)
    smartest_but_new = _row("Smartest", "Fam", 90, min_engine_version="0.40.16")
    next_best = _row("Next", "Fam", 70)
    other = _row("Other", "Other", 60)
    catalog = (smartest_but_new, next_best, other)
    picks = {
        p.family: p.recommended.name for p in sizing.recommend_by_family(16.0, catalog=catalog)
    }
    assert picks == {"Fam": "Next", "Other": "Other"}
    for mem in TIERS_GB:
        setup = sizing.suggest_local_setup(float(mem), "apple", catalog=catalog)
        assert "Smartest" not in {m.recommended.name for m in setup.members if m.recommended}
    only_new = (smartest_but_new,)
    assert [p.recommended for p in sizing.recommend_by_family(16.0, catalog=only_new)] == [None]


def test_the_cloud_sizing_is_not_filtered(shipping_0_30_10):
    new = _row("New", "F", 90, params=70.0, min_engine_version="0.40.16")
    sized = sizing.recommend(141.0, kind="gpu", concurrency=1, catalog=(new,))
    assert (
        sized.recommended is not None and sized.recommended.name == "New"
    )  # a vLLM server does not use the bundled Ollama


# ── no verdict changes while no shipped row has a requirement ─────────────────────────────────────


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    return sizing.load_catalog()


def test_no_shipped_row_names_an_engine_yet(shipped):
    assert all(m.min_engine_version == "" for m in shipped)
    assert all(sizing.runs_on_bundled_engine(m) for m in shipped)


@pytest.mark.parametrize("mem_gb", TIERS_GB)
def test_every_shipped_pick_and_suggestion_is_unchanged(shipped, mem_gb, monkeypatch):
    now = {
        p.family: (p.recommended.name if p.recommended else None)
        for p in sizing.recommend_by_family(float(mem_gb), catalog=shipped)
    }
    suggestion_now = [
        m.recommended.name
        for m in sizing.suggest_local_setup(float(mem_gb), "apple", catalog=shipped).members
        if m.recommended
    ]
    monkeypatch.setattr(
        sizing, "runs_on_bundled_engine", lambda m: True
    )  # the code as it was before this change
    before = {
        p.family: (p.recommended.name if p.recommended else None)
        for p in sizing.recommend_by_family(float(mem_gb), catalog=shipped)
    }
    suggestion_before = [
        m.recommended.name
        for m in sizing.suggest_local_setup(float(mem_gb), "apple", catalog=shipped).members
        if m.recommended
    ]
    assert now == before and suggestion_now == suggestion_before


# ── the app's picker, setup form and options ─────────────────────────────────────────────────────


def _picker_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

    rows = [
        {
            "name": "Runs Now Model",
            "ollama_tag": "runsnow:4b",
            "params_b": 4,
            "family": "Zeta",
            "intelligence": 50,
            "active_b": 4,
            "origin": "Test, US",
        },
        {
            "name": "Needs A Newer Engine",
            "ollama_tag": "newengine:4b",
            "params_b": 4,
            "family": "Zeta",
            "intelligence": 80,
            "active_b": 4,
            "origin": "Test, US",
            "min_engine_version": "99.0.0",
        },
    ]
    (tmp_path / "model_catalog.json").write_text(
        json.dumps({"generated": "2099-01-01", "models": rows})
    )
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    monkeypatch.setattr(sizing, "local_hardware", lambda: (64.0, "apple"))
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


def test_the_setup_picker_does_not_list_a_row_that_needs_a_newer_engine(tmp_path, monkeypatch):
    c, _ = _picker_app(tmp_path, monkeypatch)
    body = c.get("/setup/model").text
    assert "Runs Now Model" in body
    assert "Needs A Newer Engine" not in body and "newengine:4b" not in body


def test_the_local_model_options_leave_it_out(tmp_path, monkeypatch):
    import types

    _, app_mod = _picker_app(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [])
    options = app_mod._local_model_options(
        types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    )
    assert "runsnow:4b" in options and "newengine:4b" not in options
