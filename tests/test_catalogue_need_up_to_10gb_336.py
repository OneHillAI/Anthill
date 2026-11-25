"""Catalogue need by the calibrated rule (#336): 1.3 x file size + 1.2 GB for rows with a library file up to 10 GB."""

import json
from pathlib import Path

from anthill.hosting import sizing

SEED = Path(sizing.__file__).with_name("model_catalog.json")
# row -> file size in GB (library page, the exact q4_K_M build; qwen3.5:0.8b: top of its 1.2 to 1.3 GB page range)
FILE_GB = {
    "qwen3.5:0.8b": 1.3,
    "qwen3.5:2b": 1.9,
    "qwen3.5:4b": 3.3,
    "qwen3.5:9b": 6.6,
    "gemma4:e2b": 4.6,
    "gemma4:e4b": 6.6,
    "gemma4:12b": 8.0,
    "gemma3:1b": 0.8,
    "gemma3:4b": 3.4,
    "granite4.2:3b": 2.2,
    "granite4.2:8b": 5.3,
    "nemotron-3-nano:4b": 2.8,
    "phi4:14b": 9.1,
}


def _rows():
    return json.loads(SEED.read_text(encoding="utf-8"))["models"]


def _plain(r):
    """The row's plain tag: the older tag it replaces (#345), else its own."""
    return (r.get("replaces") or [r["ollama_tag"]])[0]


def test_the_13_rows_have_the_rule_and_nothing_else_has_a_need():
    got = {_plain(r): r.get("need_gb") for r in _rows() if "need_gb" in r}
    assert set(got) == set(FILE_GB)
    for tag, file_gb in FILE_GB.items():
        assert got[tag] == round(1.3 * file_gb + 1.2, 1), tag
        assert file_gb <= 10.0


def test_a_row_without_a_need_is_a_row_above_10_gb_or_a_cloud_row():
    for r in _rows():
        if "need_gb" not in r:
            assert _plain(r) not in FILE_GB
            assert "need_basis" not in r


def test_each_need_says_where_it_comes_from():
    for r in _rows():
        if "need_gb" in r:
            assert "1.3 x" in r["need_basis"] and "1.2 GB" in r["need_basis"]


def test_the_shipped_file_still_loads_and_the_app_reads_the_needs():
    cat = {sizing.row_tags(m)[-1]: m for m in sizing.load_catalog()}  # keyed by the plain tag
    assert all(sizing.is_safe_row(r) for r in _rows())
    for tag in FILE_GB:
        assert cat[tag].need_gb > 0.0


def test_the_verdicts_that_change_against_the_flat_formula():
    cat = {sizing.row_tags(m)[-1]: m for m in sizing.load_catalog()}  # keyed by the plain tag
    flat = {t: sizing.fit_tier(m.params_b, 16.0, active_b=m.active_b) for t, m in cat.items()}
    now = {t: sizing.model_fit_tier(m, 16.0) for t, m in cat.items()}
    changed = {t for t in cat if flat[t] != now[t]}
    assert changed == {"qwen3.5:9b", "gemma4:e4b", "gemma4:12b"}  # at 16 GB
    assert now["qwen3.5:9b"] == "tight" and now["gemma4:12b"] == "too_large"
