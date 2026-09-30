"""Pure-transform tests for the model-catalog generator (scripts/gen_model_catalog.py).

The network helpers (fetch_aa / fetch_hf / ollama_installable) are not exercised here; only the
deterministic logic that decides what gets published - intelligence merge, fill-vs-flag of metadata,
sort, validation, and new-model discovery. The generator is loaded by path (it is a script, not a
package module), the same way tests/test_connector_catalog.py loads sync_catalog.py.
"""

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SEED = _REPO / "anthill" / "hosting" / "model_catalog.json"


def _load_gen():
    spec = importlib.util.spec_from_file_location(
        "gen_model_catalog", _REPO / "scripts" / "gen_model_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_gen()


def test_sort_by_intelligence_then_params():
    rows = [
        {"name": "a", "ollama_tag": "a", "intelligence": 10, "params_b": 5},
        {"name": "b", "ollama_tag": "b", "intelligence": 50, "params_b": 1},
        {"name": "c", "ollama_tag": "c", "intelligence": 50, "params_b": 9},
    ]
    assert [r["name"] for r in gen.sort_catalog(rows)] == ["c", "b", "a"]


def test_validate_flags_missing_and_duplicates():
    errs = gen.validate_rows(
        [
            {"name": "x", "ollama_tag": ""},
            {"ollama_tag": "y"},
            {"name": "x", "ollama_tag": "x"},
            {"name": "x", "ollama_tag": "x"},
        ]
    )
    joined = " ".join(errs)
    assert "missing ollama_tag" in joined
    assert "missing name" in joined
    assert "duplicate name: x" in joined


def test_validate_passes_on_clean_rows():
    assert gen.validate_rows([{"name": "a", "ollama_tag": "a"}]) == []


def test_aa_score_extracts_flat_and_nested():
    assert gen._aa_score({"intelligence": 50}) == 50
    assert gen._aa_score({"evaluations": {"artificial_analysis_intelligence_index": 61}}) == 61
    assert gen._aa_score({"unrelated": 1}) is None


def test_apply_intelligence_overwrites_match_keeps_miss():
    rows = [
        {
            "name": "DeepSeek V4-Pro",
            "ollama_tag": "d",
            "hf_id": "deepseek-ai/DeepSeek-V4",
            "intelligence": 71,
        },
        {"name": "Unknown", "ollama_tag": "u", "hf_id": "acme/unknown", "intelligence": 40},
    ]
    idx = gen.index_intelligence(
        [{"name": "DeepSeek V4-Pro", "artificial_analysis_intelligence_index": 73}]
    )
    out, unmatched = gen.apply_intelligence(rows, idx, {})
    assert out[0]["intelligence"] == 73
    assert out[1]["intelligence"] == 40  # unmatched row keeps its seed score
    assert unmatched == ["Unknown"]


def test_apply_intelligence_matches_by_hf_id_and_name_override():
    idx = gen.index_intelligence([{"hf_id": "deepseek-ai/DeepSeek-V4", "intelligence_index": 80}])
    rows = [
        {"name": "DS", "ollama_tag": "d", "hf_id": "deepseek-ai/DeepSeek-V4", "intelligence": 1}
    ]
    out, _ = gen.apply_intelligence(rows, idx, {})
    assert out[0]["intelligence"] == 80

    idx2 = gen.index_intelligence([{"name": "Kimi K2.7 (Code)", "intelligence": 64}])
    rows2 = [{"name": "Kimi K2.7 Code", "ollama_tag": "k", "hf_id": "", "intelligence": 1}]
    out2, unmatched2 = gen.apply_intelligence(rows2, idx2, {"Kimi K2.7 Code": "Kimi K2.7 (Code)"})
    assert out2[0]["intelligence"] == 64
    assert unmatched2 == []


def test_fill_missing_meta_fills_blanks_and_flags_drift():
    rows = [
        {
            "name": "A",
            "ollama_tag": "a",
            "hf_id": "o/a",
            "params_b": 0,
            "license": "",
            "gated": False,
        },
        {
            "name": "B",
            "ollama_tag": "b",
            "hf_id": "o/b",
            "params_b": 100,
            "license": "MIT",
            "gated": False,
        },
    ]
    hf = {
        "o/a": {"params_b": 7.0, "license": "apache-2.0", "gated": None},
        "o/b": {"params_b": 100.0, "license": "MIT", "gated": True},
    }
    out, discrepancies = gen.fill_missing_meta(rows, hf)
    assert out[0]["params_b"] == 7.0
    assert out[0]["license"] == "apache-2.0"
    assert out[1]["params_b"] == 100  # a vetted value is never overwritten
    assert any("gated" in d and d.startswith("B") for d in discrepancies)


def test_fill_missing_meta_flags_param_discrepancy_without_overwriting():
    rows = [{"name": "A", "ollama_tag": "a", "hf_id": "o/a", "params_b": 10}]
    hf = {"o/a": {"params_b": 20.0, "license": "", "gated": None}}
    out, discrepancies = gen.fill_missing_meta(rows, hf)
    assert out[0]["params_b"] == 10
    assert any("params_b" in d for d in discrepancies)


def test_discover_candidates_frontier_only_and_new():
    trending = [
        {"id": "deepseek-ai/DeepSeek-V5"},
        {"id": "randomuser/some-finetune"},
        {"id": "Qwen/Qwen3.5-4B"},
    ]
    seed = [{"hf_id": "Qwen/Qwen3.5-4B"}]
    out = gen.discover_candidates(trending, seed, frozenset({"deepseek-ai", "Qwen"}))
    assert out == ["deepseek-ai/DeepSeek-V5"]


def test_offline_pipeline_over_real_seed_is_loader_valid():
    note, rows = gen.load_seed(_SEED)
    doc = gen.build_document(gen.sort_catalog(rows), note, "2026-01-01")
    assert doc["schema"] == 1
    assert len(doc["models"]) >= 10
    assert gen.validate_rows(doc["models"]) == []
    scores = [m["intelligence"] for m in doc["models"]]
    assert scores == sorted(scores, reverse=True)


# --- date stamping: an unchanged catalog must not churn a daily no-op commit -----------------------


def test_generated_keeps_the_previous_date_when_models_are_unchanged():
    rows = [{"name": "a", "ollama_tag": "a:1b"}]
    existing = {"schema": 1, "generated": "2026-01-01", "models": rows}
    # identical models -> keep the old date, so the file is byte-identical and the daily job commits nothing
    assert gen.generated_for(rows, existing, "2026-07-17") == "2026-01-01"


def test_generated_stamps_today_when_the_models_changed():
    existing = {
        "schema": 1,
        "generated": "2026-01-01",
        "models": [{"name": "a", "ollama_tag": "a:1b"}],
    }
    changed = [{"name": "a", "ollama_tag": "a:1b"}, {"name": "b", "ollama_tag": "b:2b"}]
    assert gen.generated_for(changed, existing, "2026-07-17") == "2026-07-17"
    # a score moving is a real change too
    rescored = [{"name": "a", "ollama_tag": "a:1b", "intelligence": 9}]
    assert gen.generated_for(rescored, existing, "2026-07-17") == "2026-07-17"


def test_generated_stamps_today_when_there_is_nothing_published_yet():
    rows = [{"name": "a", "ollama_tag": "a:1b"}]
    assert gen.generated_for(rows, None, "2026-07-17") == "2026-07-17"
    assert gen.generated_for(rows, {"models": rows}, "2026-07-17") == "2026-07-17"  # no usable date


def test_read_existing_tolerates_missing_and_garbage(tmp_path):
    assert gen.read_existing(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("}{ not json")
    assert gen.read_existing(bad) is None
    good = tmp_path / "good.json"
    good.write_text('{"schema": 1, "generated": "2026-01-01", "models": []}')
    assert gen.read_existing(good)["generated"] == "2026-01-01"


def test_rerunning_the_generator_offline_leaves_the_file_untouched(tmp_path):
    """The regression: two runs a day apart must produce identical bytes when nothing changed."""
    import json as _json

    out = tmp_path / "model-catalog.json"
    rows = [{"name": "a", "ollama_tag": "a:1b", "intelligence": 5}]
    out.write_text(_json.dumps(gen.build_document(rows, "note", "2026-01-01"), indent=2) + "\n")
    before = out.read_text()
    # a later run with the same rows re-stamps nothing
    again = gen.build_document(
        rows, "note", gen.generated_for(rows, gen.read_existing(out), "2026-07-17")
    )
    out.write_text(_json.dumps(again, indent=2) + "\n")
    assert out.read_text() == before
