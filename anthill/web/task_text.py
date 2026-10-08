"""Display helpers for task output (issue #89)."""

from __future__ import annotations

import re
import unicodedata

# Exactly two backslashes, a lowercase "u" and four hex digits: the double-escaped form a serialised result
# shows (``\\u00b7``). A run of three or more backslashes (an escaped backslash followed by a real escape),
# a single-backslash escape and ``\\U`` (a different escape) are the author's own text and stay as written.
_PAIR = re.compile(r"(?<!\\)\\\\u([dD][89abAB][0-9a-fA-F]{2})\\\\u([dD][c-fC-F][0-9a-fA-F]{2})")
_SINGLE = re.compile(r"(?<!\\)\\\\u([0-9a-fA-F]{4})")

# Zero-width joiners are needed inside emoji and some scripts, so they decode. Every other control or
# format character (NUL, ESC, bidi overrides, zero-width space, ...) stays as a visible escape.
_DECODE_ANYWAY = {chr(0x200C), chr(0x200D)}


def _visible(ch: str) -> bool:
    """False for a character that is invisible or can reorder text around it (control, format, line or
    paragraph separator). Zero-width joiners are needed in emoji and some scripts, so they pass."""
    return ch in _DECODE_ANYWAY or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp")


def _pair(m: re.Match) -> str:
    hi, lo = int(m.group(1), 16), int(m.group(2), 16)
    ch = chr(0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00))
    return (
        ch if _visible(ch) else m.group(0)
    )  # e.g. a Unicode tag character stays as the visible escape


def _single(m: re.Match) -> str:
    code = int(m.group(1), 16)
    if 0xD800 <= code <= 0xDFFF:  # a lone surrogate is not a character
        return m.group(0)
    ch = chr(code)
    return (
        ch if _visible(ch) else m.group(0)
    )  # invisible, or able to reorder the text: keep it visible


def display_task_text(text: str | None) -> str:
    """The text to show for a task result: double-escaped four-digit Unicode escapes become characters.

    Display only. The stored result is never changed and actions that consume the raw result (saving a
    snippet) keep receiving it as stored.
    """
    if not text or "\\\\u" not in text:
        return text or ""
    return _SINGLE.sub(_single, _PAIR.sub(_pair, text))
