"""#92: the Tasks colour tokens meet WCAG AA contrast in the light and dark themes. Model-free.

Reads the token values straight from anthill/web/static/style.css, so a later edit that lowers a ratio fails
here without a browser. The browser test (tests/browser/test_tasks_contrast.py) measures the rendered pages.
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).parent.parent / "anthill" / "web" / "static" / "style.css").read_text()


def _block(start_pattern: str) -> dict[str, str]:
    """The --token: #hex declarations of the first rule that starts with start_pattern."""
    m = re.search(start_pattern + r"\s*\{(.*?)\n\s*\}", CSS, re.S | re.M)
    assert m, start_pattern
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\b", m.group(1)))


LIGHT = _block(r"^:root")
DARK_SYSTEM = {**LIGHT, **_block(r':root:not\(\[data-theme="light"\]\)')}
DARK_FORCED = {**LIGHT, **_block(r':root\[data-theme="dark"\]')}
THEMES = {"light": LIGHT, "system-dark": DARK_SYSTEM, "forced-dark": DARK_FORCED}


def _lum(hexv: str) -> float:
    r, g, b = (int(hexv[i : i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", THEMES)
def test_ink_tokens_are_aa_on_every_surface(theme):
    t = THEMES[theme]
    for ink in (
        "--danger-text",
        "--warn-text",
        "--clay-text",
        "--muted",
        "--text",
    ):
        for surface in ("--bg", "--surface", "--bg-subtle"):
            assert _ratio(t[ink], t[surface]) >= 4.5, (
                theme,
                ink,
                surface,
                _ratio(t[ink], t[surface]),
            )


@pytest.mark.parametrize("theme", THEMES)
def test_white_text_on_the_primary_button_is_aa(theme):
    t = THEMES[theme]
    assert _ratio("#FFFFFF", t["--btn-primary-bg"]) >= 4.5
    assert _ratio("#FFFFFF", t["--btn-primary-hover"]) >= 4.5


@pytest.mark.parametrize("theme", THEMES)
def test_form_field_borders_are_at_least_3_to_1(theme):
    t = THEMES[theme]
    for surface in ("--bg", "--surface", "--bg-subtle"):
        assert _ratio(t["--control-border"], t[surface]) >= 3.0, (theme, surface)


def test_the_three_theme_blocks_define_the_same_new_tokens():
    names = {
        "--btn-primary-bg", "--btn-primary-hover", "--danger-text", "--warn-text",
        "--clay-text", "--info-dot", "--control-border",
    }  # fmt: skip
    for theme, t in THEMES.items():
        assert names <= set(t), (theme, names - set(t))
    assert DARK_SYSTEM["--danger-text"] == DARK_FORCED["--danger-text"] != LIGHT["--danger-text"]
