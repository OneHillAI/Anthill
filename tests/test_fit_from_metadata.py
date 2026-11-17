"""Memory from a model's own metadata instead of a flat guess (#223). Model-free.

``QWEN2`` and ``GRANITE`` are the attention keys recorded from a live Ollama 0.40.7 (`/api/show` of qwen2.5:3b
and granite3.2-vision:2b, 2026-11-17); ``PS_*`` are `/api/ps` readings of the same two models loaded at two
windows. The other fixtures are shaped after each architecture's documented metadata keys and were not
recorded from a model. Each expected figure is worked out by hand in a comment, not by calling the code."""

import pytest

from anthill.hosting.model_memory import (
    COMPUTE_BUFFER_BYTES_PER_TOKEN,
    arch_from_show,
    estimate_memory,
    measured_memory,
)

GB = 1e9


def _shape(a):
    """(layers, distinct KV head counts, distinct key lengths, distinct value lengths)."""
    return (
        len(a.layers),
        {layer.kv_heads for layer in a.layers},
        {layer.key_length for layer in a.layers},
        {layer.value_length for layer in a.layers},
    )


def _show(arch: str, **keys) -> dict:
    return {
        "model_info": {"general.architecture": arch, **{f"{arch}.{k}": v for k, v in keys.items()}}
    }


QWEN2 = _show(
    "qwen2",
    **{
        "block_count": 36,
        "attention.head_count": 16,
        "attention.head_count_kv": 2,
        "embedding_length": 2048,
        "context_length": 32768,
    },
)
GRANITE = _show(
    "granite",
    **{
        "block_count": 40,
        "attention.head_count": 32,
        "attention.head_count_kv": 8,
        "embedding_length": 2048,
        "context_length": 16384,
        "rope.dimension_count": 64,
    },
)

QWEN2_FILE = 1_929_912_432
PS_QWEN2 = {4096: 2_163_558_317, 8192: 2_411_567_513}
PS_GRANITE = {4096: 1_985_887_599, 8192: 2_401_144_667}


# -- reading the shape -------------------------------------------------------------------------------


def test_qwen2_shape_is_read_from_the_recorded_payload():
    a = arch_from_show(QWEN2)
    assert a is not None
    # 2048 / 16 heads = 128 per head; 2 KV heads on each of 36 layers.
    assert _shape(a) == (36, {2}, {128}, {128})
    assert a.context_length == 32768


def test_granite_shape_is_read_from_the_recorded_payload():
    a = arch_from_show(GRANITE)
    assert a is not None
    assert _shape(a) == (40, {8}, {64}, {64})  # 2048 / 32 heads


def test_explicit_key_and_value_lengths_win_over_the_even_split():
    a = arch_from_show(
        _show(
            "x",
            **{
                "block_count": 4,
                "attention.head_count": 8,
                "attention.head_count_kv": 4,
                "attention.key_length": 192,
                "attention.value_length": 128,
                "embedding_length": 4096,
            },
        )
    )
    assert a is not None and _shape(a)[2:] == ({192}, {128})


def test_per_layer_kv_heads_are_kept_per_layer():
    a = arch_from_show(
        _show(
            "x",
            **{
                "block_count": 3,
                "attention.head_count": [8, 8, 8],
                "attention.head_count_kv": [8, 4, 2],
                "embedding_length": 1024,
            },
        )
    )
    assert a is not None and tuple(layer.kv_heads for layer in a.layers) == (8, 4, 2)
    # (8 + 4 + 2) heads x (128 + 128) values x 2 bytes = 7168 bytes per token
    assert a.kv_bytes(256) == 14 * 256 * 2 * 256  # one 256-cell block


def test_no_kv_head_key_means_one_cache_head_per_attention_head():
    a = arch_from_show(
        _show("x", **{"block_count": 2, "attention.head_count": 8, "embedding_length": 1024})
    )
    assert a is not None and tuple(layer.kv_heads for layer in a.layers) == (8, 8)


@pytest.mark.parametrize(
    "show",
    [
        {},
        None,
        {"model_info": None},
        {"model_info": {}},
        {"model_info": {"general.architecture": "bert"}},  # an embedder: no attention keys
        _show(
            "x", **{"block_count": 4, "attention.head_count": 8, "embedding_length": 1001}
        ),  # uneven
        _show("x", **{"block_count": 0, "attention.head_count": 8, "embedding_length": 1024}),
        _show(
            "x",
            **{
                "block_count": 4,
                "attention.head_count": 8,
                "attention.head_count_kv": [2, 2],  # two entries for four layers
                "embedding_length": 1024,
            },
        ),
        _show("x", **{"block_count": 4, "attention.head_count": 8, "embedding_length": "1024"}),
        _show("x", **{"block_count": True, "attention.head_count": 8, "embedding_length": 1024}),
    ],
)
def test_missing_or_odd_metadata_gives_none_not_a_zero_cache(show):
    assert arch_from_show(show) is None


def test_an_unknown_cache_type_gives_no_estimate():
    a = arch_from_show(QWEN2)
    assert a is not None
    assert a.kv_bytes(4096, "bf17") is None
    assert estimate_memory(a, QWEN2_FILE, 4096, kv_type="bf17") is None


# -- the cache, worked by hand -----------------------------------------------------------------------


def test_qwen2_cache_by_hand():
    a = arch_from_show(QWEN2)
    # 36 layers x 2 KV heads x (128 + 128) x 2 bytes (f16) = 36,864 bytes per token
    assert a.kv_bytes(4096) == 36_864 * 4096
    # q8_0 stores 34 bytes per 32 values; q4_0 18 per 32.
    assert a.kv_bytes(4096, "q8_0") == pytest.approx(36 * 2 * 256 * 34 / 32 * 4096)
    assert a.kv_bytes(4096, "q4_0") == pytest.approx(36 * 2 * 256 * 18 / 32 * 4096)
    # the window is rounded up to 256 cells, as llama.cpp does
    assert a.kv_bytes(4097) == 36_864 * 4352


def test_granite_cache_by_hand():
    a = arch_from_show(GRANITE)
    # 40 layers x 8 KV heads x (64 + 64) x 2 bytes = 81,920 bytes per token, more than twice qwen2's
    # although the model is smaller: the reason a flat figure per 1,000 tokens is wrong.
    assert a.kv_bytes(1024) == 81_920 * 1024


# -- the estimate against what Ollama allocated ------------------------------------------------------


@pytest.mark.parametrize("ctx", [4096, 8192])
def test_qwen2_estimate_is_within_ten_percent_of_ollama_ps(ctx):
    est = estimate_memory(arch_from_show(QWEN2), QWEN2_FILE, ctx)
    assert est.allocated.gb == pytest.approx(PS_QWEN2[ctx] / GB, rel=0.10)


def test_the_flat_guess_over_charges_qwen2_nearly_threefold_and_the_metadata_does_not():
    # The window adds cache and compute buffer: ollama ps grew by 248.0 MB from 4,096 to 8,192 tokens. The
    # flat figure (sizing._KV_PER_K_PER_STREAM, 0.1 GB per 1,000 tokens) says 409.6 MB for the cache alone.
    grew = (PS_QWEN2[8192] - PS_QWEN2[4096]) / GB
    a = arch_from_show(QWEN2)
    est = (
        estimate_memory(a, QWEN2_FILE, 8192).allocated.gb
        - estimate_memory(a, QWEN2_FILE, 4096).allocated.gb
    )
    flat = 4.096 * 0.1
    assert abs(est - grew) / grew < 0.10
    assert flat / grew > 1.5


def test_granite_growth_with_the_window_is_within_ten_percent():
    # 40 layers x 8 x 128 x 2 = 81,920 bytes per token: close to the flat 100,000, so here it was nearly right.
    # ollama ps grew by 415.3 MB; weights cancel out, so the vision file does not matter.
    grew = (PS_GRANITE[8192] - PS_GRANITE[4096]) / GB
    a = arch_from_show(GRANITE)
    est = estimate_memory(a, 1, 8192).allocated.gb - estimate_memory(a, 1, 4096).allocated.gb
    assert abs(est - grew) / grew < 0.10


def test_a_vision_models_file_makes_the_estimate_high_never_low():
    est = estimate_memory(arch_from_show(GRANITE), 2_437_852_465, 4096)
    assert est.allocated.gb > PS_GRANITE[4096] / GB


def test_estimate_reports_four_figures_all_marked_estimated():
    est = estimate_memory(arch_from_show(QWEN2), QWEN2_FILE, 4096)
    assert {est.weight.basis, est.allocated.basis, est.resident.basis, est.peak.basis} == {
        "estimated"
    }
    assert est.weight.gb == pytest.approx(1.929912432)
    # weights + 4,096 x (36,864 cache + 22,000 compute) bytes
    assert est.allocated.gb == pytest.approx(
        (QWEN2_FILE + 4096 * (36_864 + COMPUTE_BUFFER_BYTES_PER_TOKEN)) / GB
    )
    assert est.peak.gb > est.allocated.gb >= est.resident.gb


@pytest.mark.parametrize("weight,ctx", [(0, 4096), (-5, 4096), (QWEN2_FILE, 0), (QWEN2_FILE, -1)])
def test_no_estimate_without_a_size_or_a_window(weight, ctx):
    assert estimate_memory(arch_from_show(QWEN2), weight, ctx) is None


# -- measured: the same four figures from /api/ps ----------------------------------------------------


def _ps(size, vram=None, ctx=4096):
    return {
        "name": "m",
        "size": size,
        "size_vram": size if vram is None else vram,
        "context_length": ctx,
    }


def test_measured_memory_reads_ollama_ps():
    m = measured_memory(_ps(PS_QWEN2[4096]), weight_bytes=QWEN2_FILE)
    assert (m.weight.basis, m.allocated.basis, m.resident.basis) == ("measured",) * 3
    assert m.allocated.gb == pytest.approx(2.163558317) and m.resident.gb == m.allocated.gb
    assert m.peak.basis == "estimated" and m.num_ctx == 4096


def test_measured_resident_can_be_less_than_allocated_when_part_is_offloaded():
    m = measured_memory(_ps(10_000_000_000, vram=6_000_000_000))
    assert m.allocated.gb == 10.0 and m.resident.gb == 6.0


def test_measured_weight_is_none_when_the_file_size_is_not_given():
    assert measured_memory(_ps(PS_QWEN2[4096])).weight is None


@pytest.mark.parametrize(
    "entry",
    [
        {},
        _ps(0),
        _ps(-1),
        {"size": "2163558317", "size_vram": 1},
        {"size": True, "size_vram": 1},
        {"size": 5},
    ],
)
def test_a_missing_or_odd_ps_entry_is_not_a_measurement(entry):
    assert measured_memory(entry) is None


# -- the fit rules take the model's own figure, and keep the flat guess as the fallback ---------------


def test_the_per_model_figure_replaces_the_flat_guess_in_the_fit_rules():
    from anthill.hosting import sizing

    a = arch_from_show(QWEN2)
    assert a.kv_gb_per_k(8192) == pytest.approx(0.036864)
    flat = sizing.resident_gb(3.0, context_k=8.0)
    own = sizing.resident_gb(3.0, context_k=8.0, kv_gb_per_k=a.kv_gb_per_k(8192))
    assert flat - own == pytest.approx(8.0 * (0.1 - 0.036864))


def test_without_a_per_model_figure_the_fit_rules_are_unchanged():
    from anthill.hosting import sizing

    assert sizing.resident_gb(9.0, context_k=8.0) == pytest.approx(9.0 * 0.55 + 8.0 * 0.1 + 2.5)
    assert sizing.fit_tier(9.0, 16.0) == sizing.fit_tier(9.0, 16.0, kv_gb_per_k=None)


# -- sliding window, hybrid and other shapes (fixtures shaped after the documented keys, NOT recorded) -


def _full(arch, **keys):
    return _show(arch, **keys)


GEMMA3_SHAPED = _full(
    "gemma3",
    **{
        "block_count": 12,
        "attention.head_count": 8,
        "attention.head_count_kv": 4,
        "attention.key_length": 256,
        "attention.value_length": 256,
        "attention.sliding_window": 1024,
    },
)
GPT_OSS_SHAPED = _full(
    "gptoss",  # Ollama's blobs name it without the hyphen
    **{
        "block_count": 24,
        "attention.head_count": 64,
        "attention.head_count_kv": 8,
        "attention.key_length": 64,
        "attention.value_length": 64,
        "attention.sliding_window": 128,
    },
)
HYBRID_SHAPED = _full(
    "qwen3next",
    **{
        "block_count": 8,
        "attention.head_count": 16,
        "attention.head_count_kv": 2,
        "attention.key_length": 256,
        "attention.value_length": 256,
        "ssm.conv_kernel": 4,
        "ssm.inner_size": 4096,
        "ssm.state_size": 128,
        "ssm.group_count": 16,
    },
)


@pytest.mark.parametrize(
    ("show", "hand_bytes"),
    [
        # 36 layers x 2 heads x (128 + 128) x 2 bytes x 8192 cells
        (QWEN2, 36 * 2 * 256 * 2 * 8192),
        # 40 layers x 8 heads x 128 x 2 bytes x 8192 cells
        (GRANITE, 40 * 8 * 128 * 2 * 8192),
        # 12 layers, every 6th global: 2 global x 8192 cells and 10 local x (1024 + 512 = 1536) cells,
        # each cell 4 heads x 512 x 2 bytes = 4,096 bytes
        (GEMMA3_SHAPED, (2 * 8192 + 10 * 1536) * 4096),
        # 24 layers, every 2nd global: 12 x 8192 cells and 12 x (128 + 512 = 640, padded to 768) cells,
        # each cell 8 heads x 128 x 2 bytes = 2,048 bytes
        (GPT_OSS_SHAPED, (12 * 8192 + 12 * 768) * 2048),
        # 8 layers, every 4th attention: 2 layers x 8192 cells x (2 heads x 512 x 2 bytes) = 2,048 bytes,
        # plus 6 recurrent layers of f32 state: (3 x (4096 + 2 x 16 x 128) + 128 x 4096) x 4 = 2,195,456
        (HYBRID_SHAPED, 2 * 8192 * 2048 + 6 * 2_195_456),
    ],
    ids=["qwen2", "granite", "gemma3-shaped", "gpt-oss-shaped", "hybrid-shaped"],
)
def test_five_architectures_within_ten_percent_of_the_hand_calculation(show, hand_bytes):
    a = arch_from_show(show)
    assert a is not None
    assert a.kv_bytes(8192) == pytest.approx(hand_bytes, rel=0.10)


def test_a_sliding_window_cache_stops_growing_with_the_window():
    a = arch_from_show(GPT_OSS_SHAPED)
    # Only the 12 global layers grow: 2,048 bytes per cell each.
    assert a.kv_bytes(16384) - a.kv_bytes(8192) == 12 * 8192 * 2048


def test_the_window_never_makes_a_local_layer_bigger_than_a_global_one():
    a = arch_from_show(GEMMA3_SHAPED)
    assert (
        a.kv_bytes(1024) == 12 * 1024 * 4096
    )  # the whole window fits under window + batch: all layers equal


def test_a_window_key_alone_does_not_make_an_unknown_architecture_sliding():
    # qwen2 files carry a sliding_window key it does not use; llama.cpp ignores it, and so must the estimate.
    a = arch_from_show(
        _show(
            "qwen2",
            **{
                "block_count": 4,
                "attention.head_count": 8,
                "attention.head_count_kv": 2,
                "embedding_length": 1024,
                "attention.sliding_window": 1024,
            },
        )
    )
    assert all(layer.window is None for layer in a.layers)


def test_a_per_layer_pattern_array_is_counted_as_all_global_never_less():
    show = _show(
        "gemma4",
        **{
            "block_count": 6,
            "attention.head_count": 8,
            "attention.head_count_kv": 4,
            "attention.key_length": 512,
            "attention.value_length": 512,
            "attention.key_length_swa": 256,
            "attention.value_length_swa": 256,
            "attention.sliding_window": 1024,
            "attention.sliding_window_pattern": [True, True, True, True, True, False],
        },
    )
    a = arch_from_show(show)
    assert all(layer.window is None for layer in a.layers)
    # the larger head size is used for every layer: 6 layers x 4 heads x 1024 x 2 bytes x 8192 cells
    assert a.kv_bytes(8192) == 6 * 4 * 1024 * 2 * 8192


def test_a_hybrid_with_one_kv_head_count_for_every_layer_keeps_the_cache_on_attention_layers_only():
    a = arch_from_show(HYBRID_SHAPED)
    assert [layer.kv_heads for layer in a.layers] == [0, 0, 0, 2, 0, 0, 0, 2]


def test_a_hybrid_with_a_per_layer_recurrent_list_is_not_estimated():
    show = _show("qwen35", **{**HYBRID_SHAPED["model_info"], "block_count": 8})
    show["model_info"]["qwen35.attention.recurrent_layers"] = [True, False] * 4
    show["model_info"] = {
        k.replace("qwen3next.", "qwen35."): v for k, v in show["model_info"].items()
    }
    assert arch_from_show(show) is None


def test_a_hybrid_missing_its_state_keys_is_not_estimated():
    info = {k: v for k, v in HYBRID_SHAPED["model_info"].items() if "ssm.state_size" not in k}
    assert arch_from_show({"model_info": info}) is None


def test_the_per_k_figure_is_the_average_over_the_window():
    a = arch_from_show(GPT_OSS_SHAPED)
    assert a.kv_gb_per_k(8192) == pytest.approx(a.kv_bytes(8192) / GB / 8.192)
    assert a.kv_gb_per_k(0) is None
