"""Issue #416: the local model picker must not label a model that only just fits as "recommended".
On a 16GB Mac it used to recommend a 12-14B - the sizes that froze the box (#413). Now a model that
fits but leaves little free RAM is a "runs, but tight" tier, and the recommended default is a model
that runs *well* (comfortable headroom)."""

import pytest

import anthill.hosting.sizing as sizing
import anthill.web.app as app


@pytest.mark.parametrize(
    "params_b, mem_gb, tier",
    [
        (8, 16, "recommended"),  # the founder's machine should land here
        (9, 16, "recommended"),
        (12, 16, "tight"),  # Mistral Nemo 12B: fits but ~6GB free -> tight, not recommended
        (14, 16, "too_large"),  # the 14B that froze the box
        (3, 8, "tight"),  # an 8GB Mac is tight even for a 3B (best it can do)
        (8, 8, "too_large"),
        (32, 32, "recommended"),  # a 32GB Mac runs a 32B comfortably (no regression)
        (70, 64, "recommended"),  # a 64GB Mac runs a 70B comfortably
        (70, 32, "too_large"),
    ],
)
def test_fit_tier_by_headroom(params_b, mem_gb, tier):
    assert sizing.fit_tier(params_b, mem_gb, kind="apple") == tier


def test_gpu_is_binary_fit_no_tight_tier():
    # A dedicated GPU has no OS/app contention, so there is no "tight" middle tier.
    assert sizing.fit_tier(30, 80, kind="gpu") == "recommended"
    assert sizing.fit_tier(400, 80, kind="gpu") == "too_large"


def test_picker_never_recommends_a_tight_or_too_large_model(monkeypatch):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    view = app._model_picker_view(None)  # cfg=None -> no Ollama probe

    by_name = {m["name"]: m for fam in view["families"] for m in fam["models"]}

    # nothing tight or too-large is ever the recommended default pick (#416: the tier that froze the box)
    recommended = [m for m in by_name.values() if m["recommended"]]
    assert recommended, "a 16GB box must still get at least one comfortable pick"
    for m in recommended:
        assert not m["tight"] and m["fits"], f"{m['name']} recommended but tight/too-large"

    # there is an overall default, and a frontier giant is present but greyed out (not selectable)
    assert view["default_tag"]
    giant = max(by_name.values(), key=lambda m: m["params_b"])
    assert giant["params_b"] >= 400 and giant["fits"] is False


def test_picker_still_offers_a_default_on_a_tight_machine(monkeypatch):
    # On 8GB nothing is comfortable, but the user must still get a usable (tight) default, not nothing.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (8.0, "apple"))
    view = app._model_picker_view(None)
    assert view["default_tag"], "an 8GB Mac should still get a (tight) default suggestion"
    picked = [m for fam in view["families"] for m in fam["models"] if m["recommended"]]
    assert picked and all(m["params_b"] <= 3 for m in picked)  # only the 3B-and-under tier fits 8GB


def test_picker_surfaces_offload_tier_for_a_modest_spill_moe_on_a_gpu_box(monkeypatch):
    # docs/specs/local-moe-offload-fit.md: a 12GB GPU + 32GB system RAM should offer Qwen3.6 35B-A3B
    # (a real catalog MoE) as "offload" (fits, not greyed out, labeled slower) rather than hiding it.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (12.0, "gpu"))
    monkeypatch.setattr(sizing, "gpu_box_system_ram_gb", lambda: 32.0)
    view = app._model_picker_view(None)

    by_name = {m["name"]: m for fam in view["families"] for m in fam["models"]}
    qwen = by_name["Qwen3.6 35B-A3B"]
    assert qwen["offload"] is True
    assert qwen["fits"] is True  # offload counts as fitting, not filtered like too_large
    assert qwen["tight"] is False  # "tight" is an Apple-only concept


def test_picker_never_offers_offload_when_system_ram_is_unknown(monkeypatch):
    # Same 12GB GPU box, but the system-RAM probe failed - must degrade to today's behavior (no offload
    # ever offered), never guess.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (12.0, "gpu"))
    monkeypatch.setattr(sizing, "gpu_box_system_ram_gb", lambda: None)
    view = app._model_picker_view(None)

    by_name = {m["name"]: m for fam in view["families"] for m in fam["models"]}
    assert by_name["Qwen3.6 35B-A3B"]["offload"] is False
    assert (
        by_name["Qwen3.6 35B-A3B"]["fits"] is False
    )  # falls back to plain too_large / not fitting
