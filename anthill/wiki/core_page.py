"""The core page: one small private page of a person's profile and standing preferences, in place of memory.
Spec: docs/specs/core-page.md (build plan step 2.3, slice a).

It is a normal page with the reserved slug ``core-page`` in the person's personal wiki: it has a record, an edit keeps the earlier
version, and only its owner reads it. It is never retrieved as knowledge (see ``Workspace.retrievable_pages``); it reaches the model as
guidance, in the owner's own sessions only, and the answer record lists it as an item.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..common.text import strip_frontmatter

log = logging.getLogger(__name__)

SLUG = "core-page"
TITLE = "Core page"
PROFILE_HEADING = "## Profile"
PREFERENCES_HEADING = "## Preferences"
CAP_TOKENS = 800
CAP_CHARS = CAP_TOKENS * 4  # a character count over four, not the model's tokenizer


class TooLong(ValueError):
    """A save would take the core page over its cap; ``over`` is how many characters to cut."""

    def __init__(self, over: int) -> None:
        super().__init__(
            f"The core page is kept short so it can go in every answer: please cut about {over} characters."
        )
        self.over = over


def path_of(ws) -> Path:
    return ws.wiki / f"{SLUG}.md"


def exists(ws) -> bool:
    return path_of(ws).is_file()


def is_core_page(path: Path) -> bool:
    return Path(path).stem == SLUG


def check_cap(body: str) -> None:
    """Refuse a body over the cap, saying how far over it is."""
    text = strip_frontmatter(body)
    if len(text) > CAP_CHARS:
        raise TooLong(len(text) - CAP_CHARS)


def compose(profile: str, preferences: str) -> str:
    return f"# {TITLE}\n\n{PROFILE_HEADING}\n\n{profile.strip()}\n\n{PREFERENCES_HEADING}\n\n{preferences.strip()}\n"


def split(body: str) -> tuple[str, str]:
    """The profile and the preferences of a core page body (each stripped), by the two fixed headings."""
    text = strip_frontmatter(body)
    m_pref = re.search(rf"^{re.escape(PREFERENCES_HEADING)}[ \t]*$", text, re.M)
    m_prof = re.search(rf"^{re.escape(PROFILE_HEADING)}[ \t]*$", text, re.M)
    prefs = text[m_pref.end() :].strip() if m_pref else ""
    if m_prof:
        end = m_pref.start() if m_pref and m_pref.start() > m_prof.start() else len(text)
        profile = text[m_prof.end() : end].strip()
    else:
        profile = "" if m_pref else text.strip()
    return profile, prefs


def read(ws) -> tuple[str, str]:
    """The profile and the preferences on the core page, or two empty strings when there is none."""
    path = path_of(ws)
    if not path.is_file():
        return "", ""
    return split(path.read_text(encoding="utf-8"))


def save(ws, *, profile: str | None = None, preferences: str | None = None) -> Path:
    """Write the core page as its owner's own edit, changing only the parts given. Raises ``TooLong`` over the cap."""
    old_profile, old_prefs = read(ws)
    body = compose(
        old_profile if profile is None else profile,
        old_prefs if preferences is None else preferences,
    )
    check_cap(body)
    return ws.write_page(SLUG, body, by_person=True)


def profile_text(ws, legacy_profile: str) -> str:
    """The profile as the person sees and edits it: the core page's profile when there is a core page, else the profile saved before it."""
    if exists(ws):
        return read(ws)[0]
    return legacy_profile or ""


def guidance_for(ws, legacy_profile: str) -> tuple[str, dict | None]:
    """The text the owner's turn is given as profile and preferences, and the item of the core page it came from.

    The first time this is needed and the person has a profile from before the core page, that profile becomes the core page (the old
    value is left where it was). A person with neither gets nothing and no page is made. A core page that cannot name its item is
    refused and logged: nothing from it is given."""
    from . import answer_record, prompt_blocks

    if not exists(ws):
        if not (legacy_profile or "").strip():
            return "", None
        try:
            save(ws, profile=legacy_profile.strip())
        except (TooLong, OSError) as e:
            log.warning("the profile could not be moved onto a core page: %s", e)
            return legacy_profile, None  # nothing is lost: the profile is given as it was
    profile, prefs = read(ws)
    text = f"{profile}\n\n{prefs}" if profile and prefs else (profile or prefs)
    if not text:
        return "", None
    item = answer_record._page_item(path_of(ws), ws)
    if prompt_blocks.core_page_block(text, item) is None:
        return "", None
    return text, item
