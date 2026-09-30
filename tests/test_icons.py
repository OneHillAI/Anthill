"""The icon generator (scripts/gen_icons.py) produces correct, reproducible assets:
a rounded opaque app tile, a full-bleed maskable variant, and a transparent inline glyph.
"""

import importlib.util
import pathlib

import pytest

pytest.importorskip("PIL")


def _gen():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gen_icons.py"
    spec = importlib.util.spec_from_file_location("gen_icons", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tile_is_rounded_and_correctly_sized():
    t = _gen().tile(192)
    assert t.size == (192, 192) and t.mode == "RGBA"
    assert t.getpixel((1, 1))[3] == 0  # rounded corner is transparent
    assert t.getpixel((96, 96))[3] == 255  # centre is opaque


def test_maskable_tile_is_full_bleed():
    # The maskable variant must fill the whole square so an OS mask can crop any shape
    # without leaving a transparent corner.
    t = _gen().tile(192, maskable=True)
    assert t.getpixel((1, 1))[3] == 255


def test_glyph_has_transparent_background():
    g = _gen().glyph(128)
    assert g.size == (128, 128) and g.mode == "RGBA"
    assert g.getpixel((1, 1))[3] == 0  # transparent for inline placement on any sidebar
