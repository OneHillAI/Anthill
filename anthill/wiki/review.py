"""Agent-assisted review of a proposed wiki write.

The gate every wiki write passes through before it is saved. It dry-runs the change
and decides whether it can auto-apply or must wait for a human:

- **Mechanical checks** are deterministic (new vs edit, a unified diff, dangling
  ``[[links]]``, a near-duplicate title) and never depend on the model.
- The **model pass** (contradiction, semantic duplicate, low quality) runs only for a
  **shared** (team/org) wiki. There it fails safe - an unavailable or unparseable backend
  flags ``needs_edit`` so the change is queued, never silently applied. A **personal** wiki
  skips it: a user's private notes apply immediately, gated only by the mechanical checks.
  This keeps "add a document -> it becomes usable knowledge" working for a solo user instead
  of stranding every upload behind a weak or missing local model (issue #428). A model may
  only ever set its own judgement flags, never a mechanical one.
- **PII** counts as a flag only when the page leaves the user's machine (team/org
  scope); a personal page is never flagged for PII.

Engine-level and dependency-light: takes any backend with ``.chat()`` and a
``Workspace``. The web layer turns ``flags`` into either an auto-apply or a
``WikiReview`` row.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

from ..common.text import first_h1, slugify
from ..inference.base import Message
from .workspace import Workspace

FLAG_CODES = ["contradiction", "duplicate", "pii", "broken_links", "needs_edit"]
# The subset a model is allowed to set. The mechanical flags (broken_links, pii) are determined
# deterministically by code and must never be taken from model output - a weak local model otherwise
# hallucinates them (e.g. broken_links on a link-free page), stranding clean uploads (issue #428).
_MODEL_FLAGS = frozenset({"contradiction", "duplicate", "needs_edit"})

_REVIEW_SYS = (
    "You review a proposed change to a knowledge wiki before it is saved. You are "
    "given the proposed page plus a short index of existing pages. Decide three "
    "things: does it CONTRADICT an existing page; is it a near-DUPLICATE of one; is "
    "it clearly incomplete or low quality and NEEDS_EDIT. Reply with ONE JSON object "
    'only: {"summary": "<1-2 sentences on the wiki after this change and the key '
    'delta>", "flags": [<any of "contradiction","duplicate","needs_edit">]}. An '
    "empty flags list means it is safe to apply with no human review."
)


@dataclass
class ReviewOutline:
    text: str = ""  # human-readable: post-apply state + delta
    flags: list = field(default_factory=list)  # subset of FLAG_CODES; empty => auto-apply
    recommendation: str = "approve"  # approve | needs_edit


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


def _safe_read(path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def _near_duplicate_title(new_title: str, existing: dict, exclude_slug: str):
    """Return an existing title that is a near-duplicate of new_title, else None.
    `existing` maps slug -> title."""
    nt = _norm(new_title)
    if not nt:
        return None
    for slug, title in existing.items():
        if slug == exclude_slug:
            continue
        et = _norm(title)
        if et and (nt == et or difflib.SequenceMatcher(None, nt, et).ratio() >= 0.85):
            return title
    return None


def outline_change(
    backend, ws: Workspace, slug: str, new_content: str, *, scope: str = "personal"
) -> ReviewOutline:
    """Dry-run a proposed page write and return its review. Never raises."""
    slug = slugify(slug)
    notes: list = []
    flags: list = []

    page = ws.wiki / f"{slug}.md"
    is_edit = page.exists()
    old = _safe_read(page) if is_edit else ""
    if is_edit:
        diff = list(
            difflib.unified_diff(old.splitlines(), new_content.splitlines(), lineterm="", n=0)
        )
        adds = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        dels = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        notes.append(f"Edits the existing page '{slug}' (+{adds}/-{dels} lines).")
    else:
        notes.append(f"Creates a new page '{slug}'.")

    # Mechanical: dangling [[links]] to pages that do not exist.
    existing_slugs = {p.stem for p in ws.pages()}
    refs = re.findall(r"\[\[([^\]]+)\]\]", new_content)
    dangling = sorted({r for r in refs if slugify(r) not in existing_slugs and slugify(r) != slug})
    if dangling:
        flags.append("broken_links")
        notes.append("Links to pages that do not exist: " + ", ".join(dangling[:5]) + ".")

    # Mechanical: near-duplicate title (only meaningful for a brand-new page).
    if not is_edit:
        titles = {p.stem: (first_h1(_safe_read(p)) or p.stem) for p in ws.pages()}
        dupe = _near_duplicate_title(first_h1(new_content) or slug, titles, slug)
        if dupe:
            flags.append("duplicate")
            notes.append(f"Very similar to an existing page: '{dupe}'.")

    # PII only matters when the page leaves the machine (team/org).
    if scope in ("team", "org"):
        try:
            from ..hybrid import scrub

            if scrub(new_content).had_pii:
                flags.append("pii")
                notes.append("Contains possible personal data - redact before sharing.")
        except Exception:
            pass

    # Model pass: contradiction / semantic duplicate / quality. Skipped for a PERSONAL wiki so that a
    # user's own private notes apply immediately, gated only by the mechanical checks above (real
    # dangling links, a duplicate title) - a weak or unavailable local model was otherwise flagging
    # clean personal uploads and stranding the whole "add a document -> usable knowledge" flow (issue
    # #428). Shared team/org knowledge keeps the full gate and fails safe to needs_edit when the model
    # is unavailable. A model may only set its own judgement flags, never a mechanical one (below).
    if scope in ("team", "org"):
        try:
            index = _safe_read(ws.index_md)[:2000]
            prompt = (
                f"EXISTING PAGES (index):\n{index}\n\nPROPOSED PAGE [{slug}]:\n{new_content[:3000]}"
            )
            if is_edit:
                prompt += f"\n\nIt REPLACES the current page:\n{old[:1500]}"
            obj = _parse_json(
                backend.chat([Message("system", _REVIEW_SYS), Message("user", prompt)])
            )
            summary = str(obj.get("summary", "")).strip()
            if summary:
                notes.append(summary)
            for f in obj.get("flags", []):
                # Only the model's own judgements block; a mechanical flag from model output is ignored.
                if f in _MODEL_FLAGS and f not in flags:
                    flags.append(f)
        except Exception:
            if "needs_edit" not in flags:
                flags.append("needs_edit")
            notes.append(
                "Automated review was unavailable, so this is queued for a human to check."
            )

    return ReviewOutline(
        text=" ".join(notes).strip(),
        flags=flags,
        recommendation="needs_edit" if flags else "approve",
    )


_TEXT_REVIEW_SYS = (
    "You review a proposed change to an organization's shared {kind} before it is "
    "saved for a whole team or org. Decide if it CONTRADICTS sound existing guidance "
    "or is clearly low quality and NEEDS_EDIT. Reply with ONE JSON object only: "
    '{"summary": "<1-2 sentences>", "flags": [<any of "contradiction","needs_edit">]}. '
    "An empty flags list means it is safe to apply with no human review."
)


def review_text(
    backend, content: str, *, kind: str = "principles", scope: str = "org", existing: str = ""
) -> ReviewOutline:
    """Lightweight review for scoped non-page writes (principles, skills). Never raises.

    PII is flagged only at team/org scope; the model pass fails safe to ``needs_edit``
    if the backend is unavailable or its output can't be parsed.
    """
    notes: list = []
    flags: list = []
    if scope in ("team", "org"):
        try:
            from ..hybrid import scrub

            if scrub(content).had_pii:
                flags.append("pii")
                notes.append("Contains possible personal data - redact before sharing.")
        except Exception:
            pass
    try:
        sys = _TEXT_REVIEW_SYS.replace("{kind}", kind)
        prompt = f"PROPOSED {kind.upper()}:\n{content[:3000]}"
        if existing:
            prompt += f"\n\nIt REPLACES the current {kind}:\n{existing[:1500]}"
        obj = _parse_json(backend.chat([Message("system", sys), Message("user", prompt)]))
        summary = str(obj.get("summary", "")).strip()
        if summary:
            notes.append(summary)
        for f in obj.get("flags", []):
            if f in FLAG_CODES and f not in flags:
                flags.append(f)
    except Exception:
        if "needs_edit" not in flags:
            flags.append("needs_edit")
        notes.append("Automated review was unavailable, so this is queued for a human to check.")
    return ReviewOutline(
        text=" ".join(notes).strip(),
        flags=flags,
        recommendation="needs_edit" if flags else "approve",
    )
