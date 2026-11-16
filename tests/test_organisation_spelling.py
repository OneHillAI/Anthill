"""Text a person reads uses British spelling: "organisation", never "organization" (#158).

Scans the shipped templates, the PWA manifest and the user guides, ignoring what is not prose: comments,
the `/settings/organization` and `/personalize/organization` routes and the `settings_organization`
template name (identifiers and URLs stay as they are). Model-free.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_WEB = ROOT / "anthill" / "web"
_USER_GUIDES = (
    "SLACK_BOT.md",
    "USING_ANTHILL.md",
    "how-it-works.md",
    "setup.md",
    "use-or-build-on.md",
    "using-chat-and-knowledge.md",
    "using-models-and-compute.md",
)
_US = re.compile(r"organi" + "z", re.I)  # split so this file does not match its own search

# What the scan ignores: comments, plus the identifiers that must keep the US spelling.
_COMMENTS = re.compile(r"\{#.*?#\}|<!--.*?-->|/\*.*?\*/|(?<=\s)//[^\n]*", re.S)
_IDENTIFIERS = re.compile(
    r"/(?:settings|personalize)/organi" + r"zation\S*|settings_organi" + r"zation"
)


def _prose(path: Path) -> str:
    return _IDENTIFIERS.sub("", _COMMENTS.sub("", path.read_text(encoding="utf-8")))


_FILES = sorted(
    [*(_WEB / "templates").rglob("*.html"), _WEB / "static" / "manifest.webmanifest"]
    + [ROOT / "docs" / name for name in _USER_GUIDES]
)


@pytest.mark.parametrize("path", _FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_user_facing_text_uses_british_spelling(path):
    hits = sorted({m.group(0) for m in _US.finditer(_prose(path))})
    assert not hits, f"{path.name} spells it the US way: {hits}"


def test_the_scan_finds_the_files_it_guards():
    assert len(_FILES) > 75  # templates + manifest + guides; an empty glob would pass vacuously
