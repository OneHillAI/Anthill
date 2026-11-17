"""Apple Silicon memory bandwidth: an exact chip table, by GPU cores where Apple lists two figures (#223).
Figures are Apple's published technical specifications, checked 2026-11-17. The ambient machine is never used:
every test sets the chip name and GPU cores by hand."""

import pytest

from anthill.hosting import sizing


def _chip(monkeypatch, brand, gpu_cores=None):
    monkeypatch.setattr(sizing, "_apple_chip_brand", lambda: brand)
    monkeypatch.setattr(sizing, "_apple_gpu_cores", lambda: gpu_cores)


@pytest.mark.parametrize(
    ("brand", "gpu_cores", "expected"),
    [
        ("Apple M1", None, 68.25),
        ("Apple M1 Pro", None, 200.0),  # was resolved to the base M1's 70
        ("Apple M1 Max", None, 400.0),
        ("Apple M1 Ultra", None, 800.0),
        ("Apple M2", None, 100.0),
        ("Apple M2 Pro", None, 200.0),
        ("Apple M2 Max", None, 400.0),  # was resolved to the base M2's 102
        ("Apple M2 Ultra", None, 800.0),
        ("Apple M3", None, 100.0),  # the base M3 had no entry at all
        ("Apple M3 Pro", None, 150.0),
        ("Apple M3 Max", 30, 300.0),
        ("Apple M3 Max", 40, 400.0),
        ("Apple M3 Ultra", None, 819.0),
        ("Apple M4", None, 120.0),
        ("Apple M4 Pro", None, 273.0),
        ("Apple M4 Max", 32, 410.0),
        ("Apple M4 Max", 40, 546.0),
        ("Apple M5", None, 153.0),
        ("Apple M5 Pro", None, 307.0),
        ("Apple M5 Max", 32, 460.0),  # was resolved to the base M5's 154
        ("Apple M5 Max", 40, 614.0),
    ],
)
def test_each_chip_resolves_to_its_own_figure(monkeypatch, brand, gpu_cores, expected):
    _chip(monkeypatch, brand, gpu_cores)
    assert sizing._apple_chip_bandwidth_gbps() == expected


@pytest.mark.parametrize("brand", ["Apple M4 Max", "Apple M3 Max", "Apple M5 Max"])
def test_a_max_chip_whose_gpu_cores_cannot_be_read_gives_none(monkeypatch, brand):
    _chip(monkeypatch, brand, None)
    assert sizing._apple_chip_bandwidth_gbps() is None


def test_a_max_chip_with_an_unlisted_core_count_gives_none(monkeypatch):
    _chip(monkeypatch, "Apple M4 Max", 36)
    assert sizing._apple_chip_bandwidth_gbps() is None


@pytest.mark.parametrize(
    "brand",
    [
        "Apple M9000",  # a chip not yet in the table
        "Apple M6",  # listed by Apple with a memory-dependent figure: left out on purpose
        "Apple M5 Ultra",
        "Apple M4 Pro Max",
        "M4",  # not the sysctl form
        "Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz",
        "",
        None,
    ],
)
def test_an_unseen_or_odd_chip_gives_none_never_a_neighbour(monkeypatch, brand):
    _chip(monkeypatch, brand, 10)
    assert sizing._apple_chip_bandwidth_gbps() is None


def test_the_name_must_match_whole(monkeypatch):
    # The old lookup matched "M1" inside "Apple M1 Pro" and used the base chip's figure.
    _chip(monkeypatch, "Apple M1 Pro", 16)
    assert sizing._apple_chip_bandwidth_gbps() != sizing.apple_chip_bandwidth_gbps("M1")


def test_the_table_lookup_by_chip_and_cores():
    assert sizing.apple_chip_bandwidth_gbps("M5 Max", 40) == 614.0
    assert sizing.apple_chip_bandwidth_gbps("M5 Max") is None
    assert sizing.apple_chip_bandwidth_gbps("M4 Pro", None) == 273.0
    assert sizing.apple_chip_bandwidth_gbps("nonsense") is None


def test_gpu_cores_are_only_read_when_the_chip_has_variants(monkeypatch):
    calls = []
    monkeypatch.setattr(sizing, "_apple_chip_brand", lambda: "Apple M4")
    monkeypatch.setattr(sizing, "_apple_gpu_cores", lambda: calls.append(1))
    assert sizing._apple_chip_bandwidth_gbps() == 120.0
    assert calls == []


# -- the dense speed estimate (experimental) ---------------------------------------------------------


def test_dense_estimate_matches_the_measured_qwen_run_within_ten_percent():
    # qwen2.5:3b on a base M4 (120 GB/s), 1.9299 GB file, measured 2026-11-17 on AC power:
    # median of three, 200 tokens each, 20 s apart: 42.2 tok/s.
    est = sizing.estimate_dense_decode_tokens_per_second(1.9299, bandwidth_gbps=120.0)
    assert est is not None and abs(est - 42.2) / 42.2 < 0.10


def test_dense_estimate_leans_low_on_a_vision_model_whose_file_holds_an_image_encoder():
    # granite3.2-vision:2b: 2.438 GB file, measured 49.5 tok/s on the same M4. Decoding does not read the
    # image encoder, so the estimate must not exceed what was measured.
    est = sizing.estimate_dense_decode_tokens_per_second(2.4379, bandwidth_gbps=120.0)
    assert est is not None and est < 49.5


def test_dense_estimate_is_none_when_it_cannot_be_made(monkeypatch):
    assert sizing.estimate_dense_decode_tokens_per_second(0.0, bandwidth_gbps=120.0) is None
    assert sizing.estimate_dense_decode_tokens_per_second(-1.0, bandwidth_gbps=120.0) is None
    _chip(monkeypatch, "Apple M9000")
    assert sizing.estimate_dense_decode_tokens_per_second(4.0) is None
    _chip(monkeypatch, "Apple M4")
    assert sizing.estimate_dense_decode_tokens_per_second(4.0) == pytest.approx(21.0)


def test_the_picker_speed_floor_is_unchanged(monkeypatch):
    # The experimental estimate does not feed estimate_tokens_per_second: the floor keeps its own calibration.
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: 400.0)
    llama = sizing.Model("Llama 3.3 70B", 70, active_b=70, intelligence=46)
    assert sizing.estimate_tokens_per_second(llama, kind="apple") == 400.0 / (70 * 0.5)
