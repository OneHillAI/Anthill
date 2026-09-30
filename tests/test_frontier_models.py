"""Frontier models with no self-serve path.

A handful of catalog entries are too large for EITHER path Anthill provisions today: no local machine,
and no cloud GPU node Anthill itself launches (the largest being an 8x H200-class Lambda node, per
docs/specs/multi-gpu-tensor-parallel-serving.md). Showing these as a permanently-disabled "needs more
memory" row in every family list is dead clutter regardless of what hardware a user actually has - this
classification lets the picker surface them separately instead, with a "bring your own bigger
infrastructure" note (mirrors the existing "Advanced setups... coming soon... email dev@anthill.run"
pattern already used in personalize.html).

Uses real catalog entries (anthill/hosting/model_catalog.json), not synthetic fixtures.
"""

from anthill.hosting import sizing
from anthill.hosting.sizing import Model
from anthill.web import app


def test_kimi_k3_is_in_the_real_catalog_with_verified_specs():
    # moonshotai/Kimi-K3 on HuggingFace, verified 2026-08-03: 2.8T total params, 104B active (MoE),
    # "Kimi K3 License" (the same modified-MIT shape as the existing Kimi K2.7 Code entry), ollama tag
    # confirmed live at ollama.com/library/kimi-k3:cloud (Ollama Cloud, no local GGUF path yet).
    catalog = sizing.load_catalog()
    k3 = next((m for m in catalog if m.name == "Kimi K3"), None)
    assert k3 is not None, "Kimi K3 should be in the catalog"
    assert k3.params_b == 2800
    assert k3.active_b == 104
    assert k3.family == "Kimi"
    assert k3.origin == "Moonshot AI, China"
    assert k3.license == "Modified MIT"
    assert k3.ollama_tag == "kimi-k3:cloud"
    assert k3.hf_id == "moonshotai/Kimi-K3"
    assert k3.gated is False


# --- has_any_self_serve_path / largest_self_servable_params_b -----------------------------------------


def test_kimi_k3_has_no_self_serve_path():
    k3 = next(m for m in sizing.load_catalog() if m.name == "Kimi K3")
    assert sizing.has_any_self_serve_path(k3) is False


def test_frontier_catalog_matches_exactly_these_real_entries():
    # Verified against the actual catalog, not invented - if the catalog changes, this should reflect
    # reality (mirrors test_moe_speedup.py's test_region_counts_match_the_real_catalog convention).
    catalog = sizing.load_catalog()
    frontier = {m.name for m in catalog if not sizing.has_any_self_serve_path(m)}
    assert frontier == {
        "DeepSeek V4-Pro",
        "GLM-5.1",
        "GLM-5.2",
        "Kimi K2.7 Code",
        "DeepSeek V3.1",
        "Kimi K3",
    }


def test_moderately_large_moe_still_has_a_self_serve_path():
    # MiniMax M3 (428B) and Nemotron 3 Ultra (550B) are huge, but still small enough that a maxed-out
    # local machine or Anthill's own largest cloud node could genuinely serve them - they must stay in
    # the regular family list, not get swept into "frontier".
    catalog = sizing.load_catalog()
    minimax = next(m for m in catalog if m.name == "MiniMax M3")
    nemotron_ultra = next(m for m in catalog if m.name == "Nemotron 3 Ultra")
    assert sizing.has_any_self_serve_path(minimax) is True
    assert sizing.has_any_self_serve_path(nemotron_ultra) is True


def test_dense_and_moe_giants_both_classify_correctly():
    ceiling = sizing.largest_self_servable_params_b(Model("probe", 1.0))
    just_under = Model("just under", ceiling - 1, active_b=0.0)
    just_over = Model("just over", ceiling + 1, active_b=0.0)
    assert sizing.has_any_self_serve_path(just_under) is True
    assert sizing.has_any_self_serve_path(just_over) is False


def test_a_verified_quant_build_raises_the_cloud_ceiling():
    # A model with a real 4-bit quant repo gets judged at the (much higher) quantized ceiling, matching
    # servable_on_gpu's own quantized-vs-fp16 rule - never the other way around.
    no_quant = Model("no quant", 1.0)
    with_quant = Model("with quant", 1.0, quant_hf_id="someone/some-4bit-repo")
    assert sizing.largest_self_servable_params_b(
        with_quant
    ) > sizing.largest_self_servable_params_b(no_quant)


# --- _model_picker_view integration --------------------------------------------------------------------


def test_picker_excludes_frontier_models_from_the_family_lists(monkeypatch):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    view = app._model_picker_view(None)

    by_name = {m["name"]: m for fam in view["families"] for m in fam["models"]}
    assert "Kimi K3" not in by_name
    assert "DeepSeek V4-Pro" not in by_name
    assert "GLM-5.1" not in by_name


def test_picker_surfaces_frontier_models_in_their_own_list(monkeypatch):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    view = app._model_picker_view(None)

    frontier_names = {m["name"] for m in view["frontier_models"]}
    assert frontier_names == {
        "DeepSeek V4-Pro",
        "GLM-5.1",
        "GLM-5.2",
        "Kimi K2.7 Code",
        "DeepSeek V3.1",
        "Kimi K3",
    }
    k3 = next(m for m in view["frontier_models"] if m["name"] == "Kimi K3")
    assert k3["params_b"] == 2800
    assert k3["origin"] == "Moonshot AI, China"


def test_a_family_with_zero_self_servable_members_is_dropped_entirely(monkeypatch):
    # Both real Kimi entries (K2.7 Code, K3) are frontier-only today - the Kimi family card must not
    # appear at all in the regular family list (an empty family card would be its own kind of clutter).
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    view = app._model_picker_view(None)
    family_names = {fam["family"] for fam in view["families"]}
    assert "Kimi" not in family_names


def test_a_family_with_some_self_servable_members_keeps_only_those(monkeypatch):
    # DeepSeek has both frontier (V4-Pro, V3.1) and self-servable (V4-Flash, R1 32B) members - the
    # family card must still appear, but only listing the self-servable ones.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    view = app._model_picker_view(None)
    deepseek = next(fam for fam in view["families"] if fam["family"] == "DeepSeek")
    names = {m["name"] for m in deepseek["models"]}
    assert "DeepSeek V4-Pro" not in names
    assert "DeepSeek V3.1" not in names
    assert "DeepSeek V4-Flash" in names
