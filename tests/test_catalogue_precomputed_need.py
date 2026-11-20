"""A catalogue row can carry its memory need, computed once; the picker uses it instead of guessing (#320).

`need_gb` is the memory a model occupies while running on Apple Silicon at the reference setup (an 8,192-token
window, one stream, the embedder resident). A row without a valid one is judged exactly as before. No shipped row has
one yet, so every current verdict must stay the same; the legacy copies below prove it. No network, no engine.
"""

import json
import types
from dataclasses import replace

import pytest

from anthill.hosting import sizing
from anthill.hosting.sizing import Model

TIERS_GB = (8, 16, 24, 32, 64)
BUDGET_16 = 16 * 0.66  # the usable memory the picker draws the "too large" line at on a 16 GB Mac


def _model(name="m", params=9.0, **kw):
    return Model(
        name=name,
        params_b=params,
        ollama_tag=f"{name}:x",
        family="F",
        intelligence=50.0,
        active_b=params,
        **kw,
    )


@pytest.mark.parametrize("value,expected", [(4.5, 4.5), (4, 4.0), (0.01, 0.01)])
def test_a_plausible_need_is_read(value, expected):
    assert sizing._valid_need_gb(value) == expected


@pytest.mark.parametrize(
    "value", [None, 0, -3, "9", True, False, float("nan"), float("inf"), 5000, [], {}]
)
def test_an_implausible_need_is_ignored(value):
    assert sizing._valid_need_gb(value) == 0.0


def test_a_row_carries_its_need_through_the_loader():
    row = {"name": "A", "ollama_tag": "a:4b", "params_b": 4, "need_gb": 4.1}
    assert sizing._model_from_row(row).need_gb == 4.1
    assert sizing._model_from_row({**row, "need_gb": "lots"}).need_gb == 0.0


@pytest.mark.parametrize("mem_gb", [8, 16, 24, 32, 64, 128])
@pytest.mark.parametrize("params", [1.0, 4.0, 9.0, 14.0, 30.0, 120.0])
@pytest.mark.parametrize("kind", ["apple", "gpu"])
def test_without_a_need_the_verdict_is_the_old_one(mem_gb, params, kind):
    m = _model(params=params)
    old = sizing.fit_tier(params, float(mem_gb), kind=kind, active_b=params)
    assert sizing.model_fit_tier(m, float(mem_gb), kind=kind) == old


def test_a_precomputed_need_decides_the_tier_on_a_16_gb_mac():
    flat = _model(params=9.0)  # the flat estimate says 8.25 GB: comfortable
    assert sizing.model_fit_tier(flat, 16.0) == "recommended"
    assert (
        sizing.model_fit_tier(replace(flat, need_gb=9.8), 16.0) == "tight"
    )  # measured with the embedder
    assert sizing.model_fit_tier(replace(flat, need_gb=10.5), 16.0) == "tight"
    assert sizing.model_fit_tier(replace(flat, need_gb=BUDGET_16 + 0.05), 16.0) == "too_large"
    small = _model(params=4.0)  # the flat estimate says 5.5 GB; measured 4.07 GB
    assert sizing.model_fit_tier(replace(small, need_gb=4.07), 16.0) == "recommended"
    assert (
        sizing.model_fit_tier(replace(small, need_gb=9.0), 16.0) == "recommended"
    )  # the comfort line is 7 GB free
    assert sizing.model_fit_tier(replace(small, need_gb=9.01), 16.0) == "tight"


def test_the_need_only_counts_for_the_reference_setup_on_apple_silicon():
    m = replace(_model(params=9.0), need_gb=1.0)
    assert sizing.model_fit_tier(m, 16.0, context_k=16.0) == sizing.fit_tier(
        9.0, 16.0, context_k=16.0, active_b=9.0
    )
    assert sizing.model_fit_tier(m, 16.0, concurrency=4) == sizing.fit_tier(
        9.0, 16.0, concurrency=4, active_b=9.0
    )
    assert sizing.model_fit_tier(m, 16.0, kind="gpu") == sizing.fit_tier(
        9.0, 16.0, kind="gpu", active_b=9.0
    )


def test_the_recommendations_use_the_need_too(monkeypatch):
    monkeypatch.setattr(
        sizing, "is_usable_speed", lambda m, kind: True
    )  # the speed rule is not what is tested
    big_but_small_need = replace(_model("big", params=70.0), need_gb=5.0, intelligence=90.0)
    small_but_big_need = replace(_model("small", params=2.0), need_gb=14.0, intelligence=60.0)
    plain = _model("plain", params=4.0)
    catalog = (big_but_small_need, small_but_big_need, plain)
    sized = sizing.recommend(16.0, kind="apple", concurrency=1, catalog=catalog)
    fits = {f.model.name: f.fits for f in sized.fits}
    assert fits == {"big": True, "small": False, "plain": True}
    assert sized.recommended.name == "big"
    picks = {
        p.family: p.recommended.name for p in sizing.recommend_by_family(16.0, catalog=catalog)
    }
    assert picks == {"F": "big"}
    only_flat = tuple(replace(m, need_gb=0.0) for m in catalog)
    # without the needs the flat estimate decides: the 70B is out and the 2B (higher score than "plain") is in
    assert (
        sizing.recommend(16.0, kind="apple", concurrency=1, catalog=only_flat).recommended.name
        == "small"
    )


# ── no verdict changes while no shipped row has a need ───────────────────────────────────────────


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ANTHILL_HOME", str(tmp_path)
    )  # the catalogue the app ships, no refreshed override
    return sizing.load_catalog()


def _legacy_by_family(mem_gb, catalog):
    ceiling = sizing.max_params_b(mem_gb, kind="apple", context_k=8.0, concurrency=1)
    picks = {}
    for fam in sizing.families_by_intelligence(catalog):
        best = None
        for m in (m for m in catalog if m.family == fam):
            fits = m.params_b <= ceiling and sizing.is_usable_speed(m, kind="apple")
            if fits and (
                best is None or (m.intelligence, m.params_b) > (best.intelligence, best.params_b)
            ):
                best = m
        picks[fam] = best.name if best else None
    return picks


def test_no_shipped_row_has_a_need_yet(shipped):
    assert all(m.need_gb == 0.0 for m in shipped)


@pytest.mark.parametrize("mem_gb", TIERS_GB)
def test_every_shipped_verdict_is_unchanged(shipped, mem_gb):
    for m in shipped:
        assert sizing.model_fit_tier(m, float(mem_gb)) == sizing.fit_tier(
            m.params_b, float(mem_gb), active_b=m.active_b
        )
    now = {
        p.family: (p.recommended.name if p.recommended else None)
        for p in sizing.recommend_by_family(float(mem_gb), catalog=shipped)
    }
    assert now == _legacy_by_family(float(mem_gb), shipped)
    legacy_fits = {
        m.name: m.params_b
        <= sizing.max_params_b(float(mem_gb), kind="apple", context_k=8.0, concurrency=1)
        for m in shipped
    }
    sized = sizing.recommend(float(mem_gb), kind="apple", concurrency=1, catalog=shipped)
    assert {f.model.name: f.fits for f in sized.fits} == legacy_fits


# ── the app's own guard reads it too ─────────────────────────────────────────────────────────────


def _override(tmp_path, monkeypatch, rows):
    (tmp_path / "model_catalog.json").write_text(
        json.dumps({"generated": "2099-01-01", "models": rows})
    )
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    import anthill.web.app as app_mod

    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [])
    return app_mod


def test_the_pull_guard_refuses_a_model_whose_need_is_too_big_and_allows_one_whose_need_is_small(
    tmp_path, monkeypatch
):
    rows = [
        {"name": "Tiny but hungry", "ollama_tag": "hungry:1b", "params_b": 1, "need_gb": 20},
        {"name": "Big but lean", "ollama_tag": "lean:70b", "params_b": 70, "need_gb": 5},
        {"name": "Plain 70B", "ollama_tag": "plain:70b", "params_b": 70},
    ]
    app_mod = _override(tmp_path, monkeypatch, rows)
    cfg = types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    assert app_mod._model_too_large_for_this_machine(cfg, "hungry:1b") is True
    assert app_mod._model_too_large_for_this_machine(cfg, "lean:70b") is False
    assert (
        app_mod._model_too_large_for_this_machine(cfg, "plain:70b") is True
    )  # no need: the old estimate
