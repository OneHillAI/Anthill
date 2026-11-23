"""Issue #490: the local (Apple Silicon) model recommender must size against a model's RESIDENT
footprint - weights + KV + a runner working set - not its raw q4 weights. Sizing against raw weights
over-recommended on small Macs (a 16GB M4 was told to run a 14B and froze; an 8GB box an 8B that
starved). Ground-truth anchors from the founder's machines: 16GB <= 8B, 8GB <= 3B; 24GB+ was already
fine. Local serving is single-user (concurrency 1, 8k context)."""

import pytest

from anthill.hosting import sizing

# (unified memory GB, the model the box should land on). These are the #490 evidence-table anchors.
# (unified memory GB, params_b of the smartest model that fits). Ranking is intelligence x fit now, so
# these track the frontier catalog: an MoE with few active params can win at a given memory budget.
_ANCHORS = [
    (8, 4),  # Nemotron 3 Nano 4B: need 4.8 GB by the rule (#336), under 66% of 8 GB ("tight")
    (16, 9),
    (24, 20),  # a 20B (gpt-oss) fits here
    (32, 30),  # a 30B-class MoE (GLM-4.7-flash)
    (64, 35),  # Qwen3.6 35B-A3B
]


@pytest.mark.parametrize("mem_gb, params_b", _ANCHORS)
def test_local_recommendation_matches_the_resident_budget(mem_gb, params_b):
    r = sizing.recommend(mem_gb, kind="apple", concurrency=1, context_k=8.0)
    assert r.recommended is not None, f"{mem_gb}GB should still get a recommendation"
    assert r.recommended.params_b == params_b, (
        f"{mem_gb}GB recommended {r.recommended.name} "
        f"({r.recommended.params_b}B), expected {params_b}B"
    )


def test_16gb_never_offers_the_14b_that_froze_the_box():
    """The concrete regression (#490 / #413): no family may offer a 14B on a 16GB Mac."""
    for p in sizing.recommend_by_family(16):
        assert p.recommended is None or p.recommended.params_b < 14, (
            f"{p.family} offered {p.recommended.params_b}B on 16GB"
        )


def test_8gb_never_offers_an_8b():
    for p in sizing.recommend_by_family(8):
        assert p.recommended is None or p.recommended.params_b < 8, (
            f"{p.family} offered {p.recommended.params_b}B on 8GB"
        )


def test_bigger_context_or_concurrency_shrinks_the_local_recommendation():
    """The KV budget still bites: a larger context/concurrency lowers what fits (monotonic)."""
    small = sizing.recommend(16, kind="apple", concurrency=1, context_k=8.0).recommended
    big = sizing.recommend(16, kind="apple", concurrency=1, context_k=64.0).recommended
    assert big is None or big.params_b <= small.params_b


def test_gpu_sizing_is_unchanged():
    """The #490 fix is Apple-only; the cloud-GPU (fp16) path must be untouched (~37B on an 80GB GPU)."""
    assert 35 <= sizing.cloud_gpu_max_params(80) <= 39


# Issue #153: a plain CPU box is sized with kind "apple" (the conservative fraction), but the label shown
# to the user must come from the real platform.
@pytest.mark.parametrize(
    "system, machine, mem_gb, kind, expected",
    [
        ("Darwin", "arm64", 16.0, "apple", "16 GB unified memory (Apple Silicon)"),
        ("Linux", "x86_64", 16.0, "apple", "16 GB RAM (CPU only)"),
        ("Linux", "aarch64", 8.0, "apple", "8 GB RAM (CPU only)"),
        ("Darwin", "x86_64", 32.0, "apple", "32 GB RAM (CPU only)"),
        ("Windows", "AMD64", 16.0, "apple", "16 GB RAM (CPU only)"),
        ("Linux", "x86_64", 24.0, "gpu", "24 GB GPU memory"),
        ("Linux", "x86_64", 0.0, "apple", "this machine"),
    ],
)
def test_hardware_label_follows_the_platform(monkeypatch, system, machine, mem_gb, kind, expected):
    monkeypatch.setattr(sizing.platform, "system", lambda: system)
    monkeypatch.setattr(sizing.platform, "machine", lambda: machine)
    assert sizing.hardware_label(mem_gb, kind) == expected


def test_cpu_only_linux_box_is_sized_as_apple_but_not_labelled_one(monkeypatch):
    monkeypatch.setattr(sizing.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sizing.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sizing, "_nvidia_vram_gb", lambda: None)
    monkeypatch.setattr(sizing, "_posix_ram_gb", lambda: 16.0)
    mem_gb, kind = sizing.local_hardware()
    assert (mem_gb, kind) == (16.0, "apple")
    label = sizing.hardware_label(mem_gb, kind)
    assert "Apple" not in label and "unified" not in label


def test_council_suggestion_label_is_not_the_raw_sizing_kind(monkeypatch):
    monkeypatch.setattr(sizing.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sizing.platform, "machine", lambda: "x86_64")
    s = sizing.suggest_local_setup(16.0, "apple", catalog=sizing.load_catalog())
    assert s.hw_label == "16 GB RAM (CPU only)"
