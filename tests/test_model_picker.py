"""The local (on-device) cross-family model picker: the frontier catalog grouped by family (ordered by
intelligence), the smartest-that-fits-per-family recommender, and the local hardware probe. One catalog
now serves both surfaces; see test_model_catalog.py for the loader + refresh."""

from anthill.hosting import sizing

# ── catalog shape ─────────────────────────────────────────────────────────────


def test_local_catalog_covers_the_frontier_families_with_origin():
    fams = {m.family for m in sizing.LOCAL_CATALOG}
    # the frontier is led by the Chinese labs, plus gpt-oss and the Western families in the tail
    assert {"DeepSeek", "GLM", "Qwen", "MiniMax", "Kimi", "gpt-oss", "Llama"} <= fams
    # every local model has a pullable Ollama tag and a stated origin
    for m in sizing.LOCAL_CATALOG:
        assert m.ollama_tag and m.origin and m.family
    # provenance is explicit so the picker can label it
    origins = {m.family: m.origin for m in sizing.LOCAL_CATALOG}
    assert "Meta" in origins["Llama"] and "US" in origins["Llama"]
    assert "China" in origins["Qwen"] and "China" in origins["DeepSeek"]
    assert "China" in origins["GLM"] and "US" in origins["gpt-oss"]


def test_default_local_family_is_the_strongest_intelligence_first():
    # the default family tab is the strongest by intelligence, origin-blind (the non-Chinese default is
    # retired) - it is the first entry of the intelligence-ordered family list
    assert sizing.LOCAL_FAMILIES[0] == sizing.DEFAULT_LOCAL_FAMILY
    assert sizing.DEFAULT_LOCAL_FAMILY in sizing.LOCAL_FAMILIES


# ── per-family recommendation ─────────────────────────────────────────────────


def test_recommend_by_family_returns_one_pick_per_family_in_order():
    picks = sizing.recommend_by_family(32, kind="apple")
    assert [p.family for p in picks] == list(sizing.LOCAL_FAMILIES)


def test_recommend_by_family_picks_smartest_that_fits():
    # A 16 GB Apple box (~10 GB usable for weights at 4-bit) fits ~9B, not the big ones.
    picks = {p.family: p for p in sizing.recommend_by_family(16, kind="apple")}
    assert picks["Qwen"].recommended.ollama_tag == "qwen3.5:9b"  # the smartest Qwen that fits
    assert picks["Gemma"].recommended.ollama_tag == "gemma3:4b"  # 4B fits, 27B does not
    # the frontier giants (DeepSeek/GLM/MiniMax/Kimi) have nothing small enough to fit a 16 GB box
    assert picks["DeepSeek"].recommended is None
    # the download size is reported for the UI
    assert picks["Qwen"].download_gb > 0


def test_recommend_by_family_grows_with_memory(monkeypatch):
    # A real 128GB Mac is always at least Pro/Max/Ultra-tier (base-chip configs top out well below
    # 128GB) - mock a realistic high-bandwidth chip so this test is deterministic regardless of which
    # machine actually runs it, rather than depending on ambient hardware. On a genuinely low-bandwidth
    # chip, a 70B dense model is correctly excluded as too slow (see test_moe_speedup.py) - that's not
    # a memory question, so a 128GB test needs a chip that could plausibly ship with that much RAM.
    monkeypatch.setattr(sizing, "_apple_chip_bandwidth_gbps", lambda: 819.0)  # M3 Ultra-class
    small = {p.family: p for p in sizing.recommend_by_family(16, kind="apple")}
    big = {p.family: p for p in sizing.recommend_by_family(128, kind="apple")}
    # a 128 GB machine should recommend a strictly larger Qwen than a 16 GB one
    assert big["Qwen"].recommended.params_b > small["Qwen"].recommended.params_b
    assert big["Llama"].recommended.ollama_tag == "llama3.3:70b"  # the 70B now fits


def test_recommend_by_family_none_when_too_small():
    picks = {p.family: p for p in sizing.recommend_by_family(3, kind="apple")}
    # 3 GB cannot hold even the smallest with headroom -> no recommendation, zero download
    assert picks["Llama"].recommended is None and picks["Llama"].download_gb == 0.0


def test_download_size_is_proportional_to_params():
    assert (
        sizing.family_download_gb(70) > sizing.family_download_gb(8) > sizing.family_download_gb(3)
    )


# ── local hardware probe ──────────────────────────────────────────────────────


def test_local_hardware_returns_memory_and_kind():
    mem_gb, kind = sizing.local_hardware()
    assert kind in ("apple", "gpu")
    assert mem_gb >= 0.0  # never negative; 0 only if probing failed


def test_local_hardware_uses_apple_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(sizing.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sizing.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(sizing, "_macos_mem_gb", lambda: 32.0)
    assert sizing.local_hardware() == (32.0, "apple")


def test_local_hardware_uses_gpu_when_nvidia_present(monkeypatch):
    monkeypatch.setattr(sizing.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sizing.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sizing, "_nvidia_vram_gb", lambda: 24.0)
    assert sizing.local_hardware() == (24.0, "gpu")
