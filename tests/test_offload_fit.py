"""Local MoE CPU-offload fit tier (docs/specs/local-moe-offload-fit.md).

Ollama (llama.cpp) already auto-offloads a MoE model's overflow to system RAM when it exceeds a discrete
GPU's VRAM. Decode only touches the ACTIVE experts per token, so a big MoE with routed experts in system
RAM can still be usable - but only when the spill is modest (a mostly-offloaded model is PCIe/GPU-
generation bound, not something this bandwidth-only estimate can capture). This adds an ``"offload"``
fit_tier outcome, offered only when all of: the box is `kind == "gpu"`, the model is genuinely a MoE, it
overflows VRAM but fits VRAM+RAM with headroom, the offloaded fraction stays modest, AND the resulting
speed estimate clears `_MIN_USABLE_TOK_S`.

Every model below is a REAL entry from `anthill/hosting/model_catalog.json` (params_b/active_b copied
verbatim), not a synthetic fixture, per the repo's testing convention (mirrors `tests/test_moe_speedup.py`).
"""

from anthill.hosting import sizing
from anthill.hosting.sizing import Model

# Real catalog entries (anthill/hosting/model_catalog.json), verified against the live catalog.
QWEN_35B_A3B = Model("Qwen3.6 35B-A3B", 35, active_b=3)  # the spec's "modest spill" anchor
GPT_OSS_120B = Model("gpt-oss 120B", 120, active_b=5.1)  # the spec's "mostly offloaded" anchor
NEMOTRON_3_ULTRA = Model("Nemotron 3 Ultra", 550, active_b=55)  # modest fraction, but too slow
LLAMA_3_3_70B = Model("Llama 3.3 70B", 70, active_b=70)  # dense - no offload path exists


# --- estimate_tokens_per_second (gpu-offload branch) --------------------------------------------------


def test_modest_spill_moe_matches_the_real_measured_anchor():
    # Qwen3.6 35B-A3B on a 12 GB card + 32 GB RAM measures ~50 tok/s in the build-guide data. The
    # calibrated estimate must land close to, but never above, that measured figure.
    est = sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="gpu", vram_gb=12, system_ram_gb=32)
    assert est is not None
    assert 40.0 <= est <= 50.0, f"calibration drifted from the measured ~50 tok/s anchor: {est}"


def test_mostly_offloaded_moe_cannot_be_estimated():
    # gpt-oss-120B on a 24 GB card (~3090-class) is ~72% offloaded by weight bytes - past
    # _MODEST_OFFLOAD_FRACTION_MAX, so this is refused outright (PCIe/GPU-gen bound, not a bandwidth
    # problem this estimate can capture), never an optimistic guess.
    est = sizing.estimate_tokens_per_second(GPT_OSS_120B, kind="gpu", vram_gb=24, system_ram_gb=64)
    assert est is None


def test_modest_fraction_but_still_too_slow_is_estimated_and_excluded():
    # Nemotron 3 Ultra (55B active) offloads only ~19% on a 250GB-VRAM node - well within the modest
    # threshold - but even a small offloaded slice of a 55B-active model is too slow to be usable.
    est = sizing.estimate_tokens_per_second(
        NEMOTRON_3_ULTRA, kind="gpu", vram_gb=250, system_ram_gb=500
    )
    assert est is not None
    assert est < sizing._MIN_USABLE_TOK_S, f"expected below the floor, got {est}"


def test_dense_model_over_vram_cannot_be_estimated():
    # Dense models have no offload path (every param is read every token) - always None, regardless of
    # how much RAM is available.
    est = sizing.estimate_tokens_per_second(
        LLAMA_3_3_70B, kind="gpu", vram_gb=12, system_ram_gb=500
    )
    assert est is None


def test_model_that_fits_vram_outright_is_not_an_offload_case():
    # A MoE that already fits VRAM alone isn't an offload scenario at all - None (fit_tier never even
    # reaches this branch in practice, since it checks the plain VRAM ceiling first).
    est = sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="gpu", vram_gb=80, system_ram_gb=128)
    assert est is None


def test_spill_exceeding_vram_plus_ram_cannot_be_estimated():
    # Not enough combined VRAM + system RAM to hold the model at all (with headroom) - None, same
    # degrade-safe contract as everywhere else in this file.
    est = sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="gpu", vram_gb=12, system_ram_gb=4)
    assert est is None


def test_unknown_or_zero_system_ram_cannot_be_estimated():
    assert (
        sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="gpu", vram_gb=12, system_ram_gb=None)
        is None
    )
    assert (
        sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="gpu", vram_gb=12, system_ram_gb=0)
        is None
    )


def test_apple_kind_ignores_gpu_offload_args_entirely(monkeypatch):
    # Passing vram_gb/system_ram_gb on an "apple" box must not change anything - the Apple branch never
    # looks at them.
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: 400.0)  # M3 Max
    with_args = sizing.estimate_tokens_per_second(
        QWEN_35B_A3B, kind="apple", vram_gb=12, system_ram_gb=32
    )
    without_args = sizing.estimate_tokens_per_second(QWEN_35B_A3B, kind="apple")
    assert with_args == without_args


# --- fit_tier ("offload" outcome) ----------------------------------------------------------------------


def test_modest_spill_moe_is_offered_as_offload():
    tier = sizing.fit_tier(35, 12, kind="gpu", active_b=3, system_ram_gb=32)
    assert tier == "offload"


def test_mostly_offloaded_moe_stays_too_large():
    tier = sizing.fit_tier(120, 24, kind="gpu", active_b=5.1, system_ram_gb=64)
    assert tier == "too_large"


def test_moe_below_the_speed_floor_stays_too_large():
    tier = sizing.fit_tier(550, 250, kind="gpu", active_b=55, system_ram_gb=500)
    assert tier == "too_large"


def test_dense_model_over_vram_stays_too_large():
    tier = sizing.fit_tier(70, 12, kind="gpu", active_b=70, system_ram_gb=500)
    assert tier == "too_large"


def test_unknown_system_ram_never_offers_offload():
    # Degrade-safe: without a RAM figure, behavior is identical to before this change existed.
    assert sizing.fit_tier(35, 12, kind="gpu", active_b=3, system_ram_gb=None) == "too_large"
    assert sizing.fit_tier(35, 12, kind="gpu", active_b=3, system_ram_gb=0) == "too_large"


def test_apple_never_offers_offload_even_with_ram_known():
    # Apple/unified memory has no spillover - "offload" must never appear on this branch, regardless of
    # what active_b/system_ram_gb are passed.
    tier = sizing.fit_tier(35, 32, kind="apple", active_b=3, system_ram_gb=999)
    assert tier != "offload"


def test_existing_gpu_binary_fit_behavior_is_unchanged_without_the_new_kwargs():
    # Every pre-existing call site (no active_b/system_ram_gb passed) must behave exactly as before.
    assert sizing.fit_tier(30, 80, kind="gpu") == "recommended"
    assert sizing.fit_tier(400, 80, kind="gpu") == "too_large"


def test_model_that_fits_vram_outright_is_recommended_not_offload():
    # A MoE that fits VRAM alone is "recommended" - offload is never considered when the plain ceiling
    # check already succeeds.
    tier = sizing.fit_tier(35, 80, kind="gpu", active_b=3, system_ram_gb=128)
    assert tier == "recommended"


def test_the_intelligence_tiebreak_is_completely_unmodified():
    # Same guardrail as test_moe_speedup.py: the capacity tiebreak must stay untouched by this change.
    import inspect

    src = inspect.getsource(sizing.recommend)
    assert "(m.intelligence, m.params_b) > (best.intelligence, best.params_b)" in src


# --- gpu_box_system_ram_gb probe ------------------------------------------------------------------------


def test_gpu_box_system_ram_gb_degrades_to_none_on_probe_failure(monkeypatch):
    monkeypatch.setattr(sizing, "_posix_ram_gb", lambda: None)
    monkeypatch.setattr(sizing, "_macos_mem_gb", lambda: None)
    assert sizing.gpu_box_system_ram_gb() is None
