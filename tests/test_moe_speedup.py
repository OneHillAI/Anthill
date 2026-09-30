"""On-prem MoE speed-awareness + region-aware council suggestions.

A model's MEMORY footprint scales with total params (params_b) - every expert must be resident. Its
SPEED does not - a MoE model only computes its active experts per token (active_b), so it can run far
faster than a dense model of the same total size. Neither params_b nor intelligence alone captures this.

This adds a usable-speed floor (~10 tok/s) that applies to EVERY model, dense or MoE alike - a large
dense model on modest hardware can be just as unusably slow as an under-provisioned MoE model. The floor
is a hard exclusion (not a soft preference) and is a pre-filter, separate from the existing
intelligence-based tiebreak, which must stay completely unmodified.

Tests monkeypatch `sizing._apple_chip_bandwidth_gbps` directly so results are deterministic regardless of
which machine actually runs the suite (never rely on the real, ambient hardware).
"""

from anthill.hosting import sizing
from anthill.hosting.sizing import Model


def _bw(monkeypatch, gbps):
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: gbps)


# --- estimate_tokens_per_second / is_usable_speed --------------------------------------------------


def test_calibration_matches_the_real_published_deepseek_v3_m3_ultra_result(monkeypatch):
    # DeepSeek-V3 (active_b=37, 4-bit) on an M3 Ultra (819 GB/s) measures ~17-21 tok/s in independently
    # published third-party benchmarks (VentureBeat, MacRumors, Hardware Corner, MacStories).
    _bw(monkeypatch, 819.0)
    deepseek_v3 = Model("DeepSeek V3.1", 671, active_b=37, intelligence=61)
    est = sizing.estimate_tokens_per_second(deepseek_v3, kind="apple")
    assert 17.0 <= est <= 21.0, f"calibration drifted from the measured range: {est}"


def test_dense_model_too_slow_on_modest_hardware_is_correctly_excluded(monkeypatch):
    # Llama 3.3 70B (a plain DENSE model, active_b == params_b, nothing MoE about it) estimates ~2-3.4
    # tok/s on an M1/M2/base-M4 (70-120 GB/s) - below the floor. Verified this is NOT true on M3 Max or
    # better (see the next test) - the MoE overhead factor must NOT apply to dense models (a real
    # calibration bug caught during review: applying it uniformly made this SAME model look too slow on
    # M3 Max too, contradicting real published benchmarks of ~7.5-11.2 tok/s there).
    _bw(monkeypatch, 120.0)  # base M4
    llama = Model("Llama 3.3 70B", 70, active_b=70, intelligence=46)
    est = sizing.estimate_tokens_per_second(llama, kind="apple")
    assert est < sizing._MIN_USABLE_TOK_S, f"expected below the floor, got {est}"
    assert sizing.is_usable_speed(llama, kind="apple") is False


def test_same_dense_model_is_usable_on_stronger_hardware(monkeypatch):
    # The same Llama 3.3 70B on an M3 Max (400 GB/s) or better crosses the floor - matches real published
    # benchmarks (~7.5-11.2 tok/s on M3 Max, ~12.5 tok/s on M4 Max) far better than applying the MoE
    # overhead factor would have (which would have wrongly put it at ~5.1 tok/s on M3 Max).
    _bw(monkeypatch, 400.0)  # M3 Max
    llama = Model("Llama 3.3 70B", 70, active_b=70, intelligence=46)
    assert sizing.is_usable_speed(llama, kind="apple") is True


def test_moe_overhead_factor_does_not_apply_to_dense_models(monkeypatch):
    # The actual bug this test guards against: a dense model's estimate must equal the raw
    # bandwidth-bound figure, NOT that figure discounted by _MOE_OVERHEAD_FACTOR.
    _bw(monkeypatch, 400.0)
    llama = Model("Llama 3.3 70B", 70, active_b=70, intelligence=46)
    est = sizing.estimate_tokens_per_second(llama, kind="apple")
    naive = 400.0 / (70 * sizing._BYTES_PER_PARAM_Q4)
    assert est == naive, f"a dense model must use the raw estimate, got {est} vs naive {naive}"


def test_moe_model_is_usable_where_a_same_size_dense_model_would_not_be(monkeypatch):
    # gpt-oss 20B (active_b=3.6) vs a hypothetical dense 20B (active_b=20) on the SAME modest hardware -
    # this is the actual point of MoE speed-awareness: same total size, very different real speed.
    _bw(monkeypatch, 70.0)  # M1
    moe = Model("gpt-oss 20B", 20, active_b=3.6, intelligence=48)
    dense_same_size = Model("hypothetical dense 20B", 20, active_b=20, intelligence=48)
    assert sizing.is_usable_speed(moe, kind="apple") is True
    assert sizing.is_usable_speed(dense_same_size, kind="apple") is False


def test_dense_model_with_active_b_unset_falls_back_to_params_b(monkeypatch):
    # A dense model that never had active_b populated (0.0, the dataclass default) must be treated
    # identically to one where active_b == params_b - both mean "not a MoE, use total params for speed".
    _bw(monkeypatch, 400.0)
    unset = Model("some dense model", 70, active_b=0.0, intelligence=46)
    explicit = Model("some dense model", 70, active_b=70, intelligence=46)
    assert sizing.estimate_tokens_per_second(
        unset, kind="apple"
    ) == sizing.estimate_tokens_per_second(explicit, kind="apple")


def test_unrecognized_or_non_apple_hardware_never_excludes_anything(monkeypatch):
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: None)  # unrecognized chip
    any_model = Model("anything", 70, active_b=70, intelligence=46)
    assert sizing.estimate_tokens_per_second(any_model, kind="apple") is None
    assert (
        sizing.is_usable_speed(any_model, kind="apple") is True
    )  # never excludes on unknown hardware

    # GPU-kind hosts: not modeled at all, regardless of bandwidth mock.
    _bw(monkeypatch, 819.0)
    assert sizing.estimate_tokens_per_second(any_model, kind="gpu") is None
    assert sizing.is_usable_speed(any_model, kind="gpu") is True


def test_chip_bandwidth_lookup_matches_longer_variant_names_first(monkeypatch):
    monkeypatch.setattr(sizing, "_apple_chip_brand", lambda: "Apple M4 Pro")
    assert sizing._apple_chip_bandwidth_gbps() == 273.0
    monkeypatch.setattr(sizing, "_apple_chip_brand", lambda: "Apple M4 Max")
    assert sizing._apple_chip_bandwidth_gbps() == 546.0
    monkeypatch.setattr(sizing, "_apple_chip_brand", lambda: "Apple M4")
    assert sizing._apple_chip_bandwidth_gbps() == 120.0
    monkeypatch.setattr(
        sizing, "_apple_chip_brand", lambda: "Apple M9000"
    )  # unrecognized future chip
    assert sizing._apple_chip_bandwidth_gbps() is None


# --- integration: recommend() / recommend_by_family() -----------------------------------------------


def test_the_intelligence_tiebreak_is_completely_unmodified():
    # The capacity tiebreak (larger params_b wins when intelligence ties) must be untouched by this
    # change - it answers a different question (capacity) than the speed floor (usability). This test
    # fails if that comparison's structure or inputs ever change.
    import inspect

    src = inspect.getsource(sizing.recommend)
    assert "(m.intelligence, m.params_b) > (best.intelligence, best.params_b)" in src


def test_recommend_excludes_a_too_slow_model_and_falls_back_to_a_usable_one(monkeypatch):
    _bw(monkeypatch, 70.0)  # M1
    catalog = (
        Model(
            "big dense", 70, active_b=70, intelligence=90
        ),  # smartest, but too slow here (~2 tok/s)
        Model("smaller dense", 10, active_b=10, intelligence=50),  # usable (~14 tok/s at this size)
    )
    r = sizing.recommend(200, kind="apple", catalog=catalog)  # plenty of memory for either
    assert r.recommended is not None
    assert r.recommended.name == "smaller dense"  # NOT "big dense", despite its higher intelligence
    big = next(f for f in r.fits if f.model.name == "big dense")
    assert (
        big.fits is True
    )  # memory-wise it fits - `fits` stays honest, this is not a memory problem
    assert big.too_slow is True
    assert big.recommended is False


def test_recommend_by_family_excludes_too_slow_members(monkeypatch):
    _bw(monkeypatch, 70.0)  # M1
    catalog = (
        Model("fam-a big", 70, active_b=70, intelligence=90, family="FamA"),  # ~2 tok/s, too slow
        Model("fam-a small", 10, active_b=10, intelligence=40, family="FamA"),  # ~14 tok/s, usable
    )
    picks = sizing.recommend_by_family(200, kind="apple", catalog=catalog, families=("FamA",))
    assert len(picks) == 1
    assert picks[0].recommended is not None
    assert picks[0].recommended.name == "fam-a small"


def test_unusable_hardware_falls_back_to_todays_memory_only_behavior(monkeypatch):
    # No bandwidth data at all (e.g. CI on Linux, or an unrecognized chip): the speed floor never fires,
    # behavior is identical to before this change.
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: None)
    catalog = (Model("big dense", 70, active_b=70, intelligence=90),)
    r = sizing.recommend(200, kind="apple", catalog=catalog)
    assert r.recommended is not None
    assert r.recommended.name == "big dense"


# --- region-aware council suggestions ----------------------------------------------------------------


def test_region_counts_match_the_real_catalog():
    # Verified against the actual 27-model catalog (anthill/hosting/model_catalog.json), not invented
    # fixtures - if the real catalog ever changes, this test should reflect reality.
    catalog = sizing.load_catalog()
    china = sizing.suggest_regional_council("China", catalog=catalog)
    us = sizing.suggest_regional_council("US", catalog=catalog)
    eu = sizing.suggest_regional_council("EU", catalog=catalog)
    assert set(china.families) == {"DeepSeek", "MiniMax", "GLM", "Kimi", "Qwen"}
    assert china.diverse is True
    assert set(us.families) == {"gpt-oss", "Llama", "Gemma", "Phi", "Nemotron"}
    assert us.diverse is True
    assert eu.families == ("Mistral",)
    assert eu.diverse is False
    assert "Mistral" in eu.note  # the scarcity is named, not hidden


def test_region_suggestion_is_honest_about_scarcity_not_silent():
    catalog = (Model("only one", 24, family="Mistral", origin="Mistral AI, France (EU)"),)
    result = sizing.suggest_regional_council("EU", catalog=catalog)
    assert result.diverse is False
    assert "isn't achievable" in result.note or "not achievable" in result.note.lower()


def test_region_of_matches_known_origin_patterns():
    assert sizing._region_of("Meta, US") == "US"
    assert sizing._region_of("Mistral AI, France (EU)") == "EU"
    assert sizing._region_of("Alibaba, China") == "China"
    assert sizing._region_of("") is None
    assert sizing._region_of("somewhere unrecognized") is None
