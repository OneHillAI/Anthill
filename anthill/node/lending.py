"""Knowledge lending by area enforced in Python outside the model + per-peer quotas (Rules 3, 8, 13).

Private and un-lent wiki areas are physically excluded before any prompt is constructed.
Raw pages never leave the node; only bounded excerpts from explicitly allow-listed,
non-locked areas are passed into the 0-tool peer consultation handler.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from ..wiki import okf
from ..wiki.workspace import Workspace
from . import PEER_RATE_LIMIT_PER_HOUR

SENSITIVE_TAGS = frozenset(
    {"private", "sensitive", "personal-private", "health", "finance", "secret", "hr", "legal"}
)
LOCKED_AREA_ID = "personal-private"


@dataclass(frozen=True)
class WikiAreaInfo:
    area_id: str
    area_name: str
    scope_default: str
    locked: bool
    page_count: int


def classify_page_area(
    slug: str, page: okf.OkfPage, *, scope: str = "personal", team_label: str = ""
) -> tuple[str, str, bool]:
    """Return ``(area_id, area_name, locked)`` for one OKGF wiki page."""
    tags = {str(t).strip().lower() for t in (page.tags or []) if str(t).strip()}
    if tags & SENSITIVE_TAGS or slug.startswith(("private-", "secret-", "health-", "finance-")):
        return LOCKED_AREA_ID, "Personal / Sensitive", True

    for tag in sorted(tags):
        if tag.startswith("area:"):
            suffix = tag.split(":", 1)[1].strip()
            if suffix:
                pretty = suffix.replace("-", " ").replace("_", " ").title()
                return f"area:{suffix}", pretty, False
        if tag in ("public", "published"):
            return "public-answers", "Public & Published Answers", False

    if scope == "org":
        return "org-wiki", "Org Wiki Areas", False
    if scope == "team":
        label = team_label or "Project Wiki"
        area_slug = label.lower().replace(" ", "-")
        return f"project:{area_slug}", f"Project: {label}", False
    return "personal-notes", "Personal Notes", False


def default_allowed_for_hill(area_id: str, hill_scope: str, locked: bool) -> bool:
    """Brief Sec 4.4 defaults: Private=none, Internal=org areas, Open=public only."""
    if locked or area_id == LOCKED_AREA_ID:
        return False
    if hill_scope == "internal":
        return area_id == "org-wiki"
    if hill_scope == "open":
        return area_id == "public-answers"
    return False


def discover_wiki_areas(
    workspaces: list[tuple[str, Workspace, str]],
) -> list[WikiAreaInfo]:
    """Scan local workspaces and summarize distinct wiki areas (always including locked personal)."""
    counts: dict[str, int] = {LOCKED_AREA_ID: 0, "personal-notes": 0, "public-answers": 0}
    names: dict[str, str] = {
        LOCKED_AREA_ID: "Personal / Sensitive",
        "personal-notes": "Personal Notes",
        "public-answers": "Public & Published Answers",
    }
    locked_map: dict[str, bool] = {
        LOCKED_AREA_ID: True,
        "personal-notes": False,
        "public-answers": False,
    }
    defaults: dict[str, str] = {
        LOCKED_AREA_ID: "Never lent",
        "personal-notes": "Private (none until chosen)",
        "public-answers": "Public only",
    }

    for scope, ws, team_label in workspaces:
        if not ws.wiki.exists():
            continue
        for p in ws.retrievable_pages():
            try:
                page = okf.parse_okf(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            aid, aname, is_locked = classify_page_area(
                p.stem, page, scope=scope, team_label=team_label
            )
            counts[aid] = counts.get(aid, 0) + 1
            names[aid] = aname
            locked_map[aid] = is_locked
            if aid not in defaults:
                defaults[aid] = (
                    "Never lent"
                    if is_locked
                    else "Org default"
                    if aid == "org-wiki"
                    else "Private (none until chosen)"
                )

    result: list[WikiAreaInfo] = []
    for aid in sorted(counts, key=lambda k: (locked_map.get(k, False), names.get(k, k))):
        result.append(
            WikiAreaInfo(
                area_id=aid,
                area_name=names[aid],
                scope_default=defaults.get(aid, "Private (none until chosen)"),
                locked=locked_map.get(aid, False),
                page_count=counts[aid],
            )
        )
    return result


def select_lent_excerpts(
    workspaces: list[tuple[str, Workspace, str]],
    *,
    allowed_areas: set[str],
    locked_areas: set[str] | None = None,
    max_pages: int = 8,
    max_chars_per_page: int = 900,
) -> list[dict[str, str]]:
    """Physically filter wiki pages in Python to explicitly lent, non-locked areas only.

    Returns bounded excerpts (never raw full pages). Any page in a locked or un-lent area
    is dropped before model context construction (Rule 3).
    """
    locked = set(locked_areas or ()) | {LOCKED_AREA_ID}
    effective_allowed = {a for a in allowed_areas if a not in locked}
    if not effective_allowed:
        return []

    excerpts: list[dict[str, str]] = []
    for scope, ws, team_label in workspaces:
        if not ws.wiki.exists():
            continue
        for p in ws.retrievable_pages():
            if len(excerpts) >= max_pages:
                return excerpts
            try:
                page = okf.parse_okf(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            aid, aname, is_locked = classify_page_area(
                p.stem, page, scope=scope, team_label=team_label
            )
            if is_locked or aid in locked or aid not in effective_allowed:
                continue
            body = (page.body or "").strip()
            if not body:
                continue
            excerpts.append(
                {
                    "source_id": p.name,
                    "title": page.title or p.stem,
                    "area_id": aid,
                    "area_name": aname,
                    "excerpt": body[:max_chars_per_page],
                }
            )
    return excerpts


class PeerRateLimiter:
    """In-memory sliding-window per-peer consultation quota (default: 20 req/hr)."""

    # ponytail: in-memory deque per process; persist in SQLite only if multi-worker deployment is added.
    def __init__(
        self, max_requests: int = PEER_RATE_LIMIT_PER_HOUR, window_seconds: int = 3600
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, peer_key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Record a hit if under quota. Returns ``(allowed, remaining_quota)``."""
        t = time.monotonic() if now is None else now
        cutoff = t - self.window_seconds
        with self._lock:
            q = self._hits.setdefault(peer_key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False, 0
            q.append(t)
            return True, self.max_requests - len(q)

    def remaining(self, peer_key: str, *, now: float | None = None) -> int:
        t = time.monotonic() if now is None else now
        cutoff = t - self.window_seconds
        with self._lock:
            q = self._hits.get(peer_key)
            if not q:
                return self.max_requests
            while q and q[0] <= cutoff:
                q.popleft()
            return max(0, self.max_requests - len(q))
