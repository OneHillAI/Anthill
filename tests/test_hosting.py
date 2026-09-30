"""Org hosting foundation: the three hosting tiers + the hardware-aware model-sizing recommender."""

import pytest

from anthill.hosting import sizing
from anthill.hosting.tiers import TIER_KEYS, TIERS, tier

# ── tiers ────────────────────────────────────────────────────────────────────────


def test_three_tiers_present():
    assert set(TIERS) == set(TIER_KEYS) == {"onprem", "vpc", "neocloud"}


def test_green_tiers_are_always_on_and_have_no_caveat():
    for key in ("onprem", "vpc"):
        t = tier(key)
        assert t.sovereignty == "green"
        assert t.always_on is True
        assert t.caveat == ""


def test_neocloud_is_amber_per_use_with_a_caveat():
    t = tier("neocloud")
    assert t.sovereignty == "amber"
    assert t.always_on is False  # per-use / serverless
    assert "shared GPUs" in t.caveat and "cold start" in t.caveat


def test_unknown_tier_raises():
    with pytest.raises(KeyError):
        tier("onehill-hosted")  # there is no such thing - Onehill hosts nothing


# ── sizing: the recommendation envelope ──────────────────────────────────────────
# Calibrated targets from the spec: 32GB Mac -> ~32B, 64GB Mac -> ~70B, 80GB GPU -> 70B,
# 2x80GB -> a 235B-class MoE. We assert the recommended size, not the raw arithmetic.


@pytest.mark.parametrize(
    "mem_gb,kind,expected_b",
    [
        # smartest that fits (intelligence x fit over the frontier catalog), not merely the largest
        (16, "apple", 9),
        (24, "apple", 20),  # a 20B (gpt-oss) fits this envelope
        (32, "apple", 30),  # the $1k Mac mini -> a 30B-class MoE
        (64, "apple", 35),  # Qwen3.6 35B-A3B (35B resident, 3B active)
        (80, "gpu", 122),  # one big GPU -> a 122B MoE
        (160, "gpu", 122),  # two big GPUs; the frontier giants (284B+) still need more
    ],
)
def test_recommended_model_matches_envelope(mem_gb, kind, expected_b):
    # The envelope is the single-user capacity of the box (concurrency 1); the Apple sizes here are
    # personal machines, not shared servers. Sizing accounts for the model's resident footprint, so at
    # a heavier concurrent load the recommendation is correctly smaller (issue #490).
    rec = sizing.recommend(mem_gb, kind=kind, concurrency=1)
    assert rec.recommended is not None
    assert rec.recommended.params_b == expected_b


def test_too_large_models_are_flagged_not_silently_dropped():
    rec = sizing.recommend(
        16, kind="apple"
    )  # default concurrency=4 -> a smaller pick than single-user
    assert rec.recommended.params_b == 4  # a 4B is the pick at this (multi-stream) envelope
    # the frontier giant is present in the annotated list, flagged too-large, and not picked
    big = max(rec.fits, key=lambda m: m.model.params_b)
    assert big.model.params_b >= 400 and not big.fits and not big.recommended


def test_recommended_is_consistent_with_fits():
    rec = sizing.recommend(80, kind="gpu")
    picked = [m for m in rec.fits if m.recommended]
    assert len(picked) == 1 and picked[0].fits  # exactly one recommendation, and it fits


def test_concurrency_shrinks_the_recommendation():
    light = sizing.max_params_b(80, kind="gpu", concurrency=2)
    heavy = sizing.max_params_b(80, kind="gpu", concurrency=32)
    assert heavy < light  # more concurrent users -> bigger KV cache -> smaller model


def test_long_context_shrinks_the_recommendation():
    short = sizing.max_params_b(80, kind="gpu", context_k=8)
    long = sizing.max_params_b(80, kind="gpu", context_k=128)
    assert long < short


def test_gpu_holds_more_than_apple_for_the_same_memory():
    assert sizing.max_params_b(64, kind="gpu") > sizing.max_params_b(64, kind="apple")


def test_a_box_too_small_recommends_nothing():
    rec = sizing.recommend(4, kind="apple")  # 4GB can't serve any catalog model with headroom
    assert rec.recommended is None
    assert all(not m.fits for m in rec.fits)


# ── cloud GPU tiers (gate the model picker) ───────────────────────────────────────


def test_gpu_tiers_are_ordered_and_carry_a_runpod_pool():
    keys = [t.key for t in sizing.GPU_TIERS]
    assert keys == ["24", "48", "80", "141"]  # ascending VRAM
    vram = [t.vram_gb for t in sizing.GPU_TIERS]
    assert vram == sorted(vram)
    assert all(t.runpod_pool for t in sizing.GPU_TIERS)  # each maps to a provisioning pool id


def test_gpu_tier_lookup():
    assert sizing.gpu_tier("80").vram_gb == 80
    assert sizing.gpu_tier(" 24 ").runpod_pool == "AMPERE_24"  # trimmed
    assert sizing.gpu_tier("nope") is None
    assert sizing.gpu_tier("") is None


def test_model_fits_vram_uses_full_precision():
    # cloud GPUs serve at fp16 (vLLM default), so fit is much stricter than the 4-bit on-prem envelope:
    # a 70B does not fit even a 141 GB GPU; the small tiers hold only a few-billion model
    assert sizing.model_fits_vram(8, 24) and not sizing.model_fits_vram(14, 24)
    assert sizing.model_fits_vram(14, 48) and not sizing.model_fits_vram(32, 48)
    assert sizing.model_fits_vram(32, 80) and not sizing.model_fits_vram(70, 80)
    assert not sizing.model_fits_vram(70, 141)  # 70B at fp16 needs more than one GPU / quantization


def test_cloud_gpu_max_params_is_fp16_and_stricter_than_4bit():
    # ~2 GB per billion (fp16): an 80 GB GPU serves ~37B, not the ~130B the 4-bit math would claim
    assert 30 <= sizing.cloud_gpu_max_params(80) <= 45
    assert sizing.cloud_gpu_max_params(80) < sizing.max_params_b(80)  # fp16 is stricter than 4-bit


def test_quantized_ceiling_unlocks_big_models_on_a_single_gpu():
    # 4-bit (AWQ) is ~a quarter of fp16, so a curated 32B or 70B fits a 48 GB GPU - the point of the
    # toggle. The ceiling keeps the same 8k-context headroom the fp16 path uses, so it stays honest.
    # 32B and 70B both clear the 48 GB tier at 4-bit, where neither fits at full precision.
    assert sizing.cloud_gpu_max_params_quantized(48) >= 70
    assert not sizing.model_fits_vram(32, 48) and not sizing.model_fits_vram(70, 48)  # fp16 too big
    # and 4-bit is always more permissive than full precision on the same card
    for vram in (24, 48, 80):
        assert sizing.cloud_gpu_max_params_quantized(vram) > sizing.cloud_gpu_max_params(vram)
    assert sizing.cloud_gpu_max_params(141) < 70  # too big even for the largest GPU at fp16
    assert sizing.cloud_gpu_max_params_quantized(48) >= 70  # but fits a 48 GB GPU at 4-bit


def test_servable_on_gpu_is_the_honest_single_gpu_gate():
    # fp16 by default: a model at or under the full-precision ceiling serves, above it does not.
    assert sizing.servable_on_gpu(32, 80) and not sizing.servable_on_gpu(70, 80)
    # 4-bit roughly triples the ceiling, but ONLY when the model actually has a quant build.
    assert sizing.servable_on_gpu(70, 48, quantized=True, has_quant=True)  # fits at 4-bit
    assert not sizing.servable_on_gpu(70, 48, quantized=True, has_quant=False)  # no build -> fp16
    assert not sizing.servable_on_gpu(70, 48, quantized=False, has_quant=True)  # toggle off -> fp16
    # even with a real quant build, a card too small at 4-bit still refuses (70B needs > 24 GB at 4-bit).
    assert not sizing.servable_on_gpu(70, 24, quantized=True, has_quant=True)
    # degenerate inputs never claim serveability
    assert not sizing.servable_on_gpu(0, 80) and not sizing.servable_on_gpu(7, 0)


def test_servable_on_gpu_aggregates_vram_across_a_tensor_parallel_node():
    # docs/specs/multi-gpu-tensor-parallel-serving.md's own example: a 671B model at 4-bit fits an
    # 8x141GB node's aggregate VRAM but not a single 141GB GPU.
    assert sizing.servable_on_gpu(671, 141, quantized=True, has_quant=True, gpu_count=8)
    assert not sizing.servable_on_gpu(
        671, 141, quantized=True, has_quant=True
    )  # gpu_count=1 default
    # gpu_count=1 (default/explicit) reproduces every single-GPU case exactly - no behavior change.
    assert sizing.servable_on_gpu(32, 80, gpu_count=1) == sizing.servable_on_gpu(32, 80)
    assert not sizing.servable_on_gpu(70, 80, gpu_count=1)


def test_usable_gb_multiplies_overhead_by_gpu_count_but_kv_stays_a_flat_shared_pool():
    # docs/specs/multi-gpu-tensor-parallel-serving.md's exact formula: gpu_count * vram - gpu_count *
    # overhead - kv. VRAM and the per-GPU overhead both scale with gpu_count; the KV budget does not -
    # it is one shared pool across the tensor-parallel group, not multiplied per GPU.
    aggregate = sizing.usable_gb(80, kind="gpu", context_k=8.0, concurrency=1, gpu_count=8)
    kv = 8.0 * 1 * 0.1  # _kv_budget_gb(context_k=8, concurrency=1) = context_k * concurrency * 0.1
    assert aggregate == pytest.approx(8 * 80 - 8 * sizing._GPU_OVERHEAD_GB - kv)
    # gpu_count=1 (default) reproduces the pre-existing single-GPU formula exactly.
    assert sizing.usable_gb(
        80, kind="gpu", context_k=8.0, concurrency=1, gpu_count=1
    ) == sizing.usable_gb(80, kind="gpu", context_k=8.0, concurrency=1)


def test_gpu_count_divides_heads():
    assert sizing.gpu_count_divides_heads(64, 8)
    assert not sizing.gpu_count_divides_heads(66, 8)
    assert sizing.gpu_count_divides_heads(0, 8)  # unknown heads: non-blocking
    assert sizing.gpu_count_divides_heads(66, 1)  # gpu_count=1: never blocks (no tensor-parallel)


def test_servable_id_resolves_the_quant_repo_only_for_cloud():
    from anthill.hosting import source

    # cloud (prefer_hf) + quantized -> the verified AWQ repo, so vLLM serves it 4-bit automatically
    assert (
        source.servable_id("Llama 3.3 70B", prefer_hf=True, quantized=True)
        == "casperhansen/llama-3.3-70b-instruct-awq"
    )
    # without the flag it is the full-precision repo
    assert (
        source.servable_id("Llama 3.3 70B", prefer_hf=True, quantized=False)
        == "meta-llama/Llama-3.3-70B-Instruct"
    )
    # on-prem (Ollama) ignores quantized - it serves its own tag
    assert source.servable_id("Llama 3.3 70B", prefer_hf=False, quantized=True) == "llama3.3:70b"
    # a model with no quant build falls back to its normal repo even when asked for 4-bit
    assert source.servable_id("Phi-4 14B", prefer_hf=True, quantized=True) == "microsoft/phi-4"


def test_builtin_catalog_exposes_quant_availability():
    from anthill.hosting import source

    by_name = {m.name: m for m in source.builtin_catalog()}
    assert by_name["Llama 3.3 70B"].has_quant  # the curated 70B has a verified 4-bit build
    assert not by_name["Phi-4 14B"].has_quant  # most models have no curated 4-bit build
