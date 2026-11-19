"""Catalogue release 1 (#304): the bundled seed is backfilled but stays readable by the app code that reads it."""

import json
from pathlib import Path

from anthill.hosting import sizing

SEED = Path(sizing.__file__).with_name("model_catalog.json")
GGUF_TAGS = {
    "qwen3.5:0.8b": "qwen3.5:0.8b-q8_0",
    "qwen3.5:2b": "qwen3.5:2b-q4_K_M",
    "qwen3.5:4b": "qwen3.5:4b-q4_K_M",
    "qwen3.5:9b": "qwen3.5:9b-q4_K_M",
    "qwen3.5:27b": "qwen3.5:27b-q4_K_M",
    "qwen3.5:122b": "qwen3.5:122b-a10b-q4_K_M",
    "qwen3.8:27b": "qwen3.8:27b-q4_K_M",
    "gemma4:e2b": "gemma4:e2b-it-q4_K_M",
    "gemma4:e4b": "gemma4:e4b-it-q4_K_M",
    "gemma4:12b": "gemma4:12b-it-q4_K_M",
    "gemma4:26b": "gemma4:26b-a4b-it-q4_K_M",
    "gemma4:31b": "gemma4:31b-it-q4_K_M",
}
TIERS = {"8", "16", "24", "32", "64"}
VERDICTS = {"fits", "tight", "too_large", "not_computed"}


def _rows():
    return json.loads(SEED.read_text(encoding="utf-8"))["models"]


def test_every_row_is_safe_and_parses_in_the_app_code():
    rows = _rows()
    assert len(rows) == 43
    assert all(sizing.is_safe_row(r) for r in rows)
    assert len(sizing.load_catalog()) == 43


def test_scores_are_unscored_but_stay_numbers():
    for r in _rows():
        assert r["intelligence"] == 0 and isinstance(r["intelligence"], int | float)
        assert r["score"]["status"] == "unscored"
    assert "artificialanalysis" not in SEED.read_text(encoding="utf-8").lower()


def test_the_explicit_gguf_tags_are_stored_and_the_plain_tags_are_unchanged():
    found = {}
    for r in _rows():
        if "gguf_tag" in r:
            assert sizing.is_safe_ollama_tag(r["gguf_tag"])
            assert r["gguf_tag"].startswith(r["ollama_tag"].split(":")[0] + ":")
            found[r["ollama_tag"]] = r["gguf_tag"]
    assert found == GGUF_TAGS


def test_local_rows_have_a_fit_table_and_cloud_rows_none():
    for r in _rows():
        if r["ollama_tag"].endswith(":cloud"):
            assert r["cloud"] is True and not r["fit"]
        else:
            assert r["cloud"] is False
            assert set(r["fit"]) == TIERS and set(r["fit"].values()) <= VERDICTS


def test_licence_fields_agree_and_unverified_regions_say_so():
    for r in _rows():
        assert r["licence"]["name"].startswith(r["license"])
        assert r["region_verified"] is False
        assert r["validation"] in {"validated", "unvalidated"}
    validated = [r["ollama_tag"] for r in _rows() if r["validation"] == "validated"]
    assert validated == ["qwen3.5:4b"]
