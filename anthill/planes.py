"""Personal (solo) vs Organization (org) planes - the routing rule (two-plane architecture, P2).

Every conversation and scheduled task runs in exactly one plane:

  solo - always LOCAL: the user's own device model + personal wiki. Private and offline-capable;
         personal context never leaves the device.
  org  - always the organization's CLOUD-hosted shared model + shared wiki. Requires a live
         connection; offline it is simply unavailable (like a hosted assistant) and never silently
         falls back to the local model.

This module is the *pure* routing rule: given a plane (and, for availability, the org config) it
says which model source and wiki scope to use and whether a connection is required. It performs no
inference and imports no DB models - the chat/task executors consult it to choose a backend. See
engineering-plans/PERSONAL_AND_ORG_PLANES.md (internal).
"""

from __future__ import annotations

from dataclasses import dataclass

PLANES = ("solo", "team", "org")
DEFAULT_PLANE = "solo"

# Org-backend statuses that mean "there is a usable shared model to route to."
_READY_STATUSES = ("validated", "provisioned")


def normalize(plane: str | None) -> str:
    """Map any input to a canonical plane key; unknown / blank -> the safe default (solo)."""
    p = (plane or "").strip().lower()
    return p if p in PLANES else DEFAULT_PLANE


@dataclass(frozen=True)
class Route:
    """How a plane resolves: where the model and wiki come from, and the connectivity contract."""

    plane: str  # solo | team | org
    model_source: str  # "local" | "org"
    wiki_scope: str  # "personal" | "team" | "org"
    requires_connection: bool  # org (and team-in-org) need a live backend; solo never does
    offline_ok: bool  # solo (and team-in-solo) work offline; org does not
    reads_org: bool = False  # team in an org also grounds in the org wiki, read-only


def route(plane: str | None, *, org_mode: bool = False) -> Route:
    """The routing rule for a plane. Org -> shared cloud model + org wiki (needs a connection);
    solo -> local model + personal wiki (offline-capable). Team -> the team wiki, on the org cloud
    model + org-wiki read when this install is an org (``org_mode``), or the local model when Solo.
    Unknown input routes to solo."""
    p = normalize(plane)
    if p == "org":
        return Route(
            plane="org",
            model_source="org",
            wiki_scope="org",
            requires_connection=True,
            offline_ok=False,
        )
    if p == "team":
        if org_mode:
            return Route(
                plane="team",
                model_source="org",
                wiki_scope="team",
                requires_connection=True,
                offline_ok=False,
                reads_org=True,
            )
        return Route(
            plane="team",
            model_source="local",
            wiki_scope="team",
            requires_connection=False,
            offline_ok=True,
        )
    return Route(
        plane="solo",
        model_source="local",
        wiki_scope="personal",
        requires_connection=False,
        offline_ok=True,
    )


def org_available(cfg) -> bool:
    """True when the org plane has a usable backend to route to: a validated (or provisioned) shared
    model. ``cfg`` is an ``OrgSettings``-like object (duck-typed); None / no backend -> False.

    A merely *planned* selection is not available yet - only an end-to-end validated endpoint (or a
    live provisioned one) counts, so the chat UI never offers Org chat against a backend that cannot
    actually answer.
    """
    if cfg is None:
        return False
    status = (getattr(cfg, "org_backend_status", "") or "").strip().lower()
    return status in _READY_STATUSES


def is_org_mode(cfg) -> bool:
    """True when this install IS an organization: an org backend has been configured at some point,
    regardless of whether it is reachable right now. This is the migration/visibility signal - in an
    org there is an org wiki and teams are collaborative; in Solo (no org configured) there is no org
    wiki and teams are local projects. Distinct from ``org_available`` (which needs the backend live
    now to actually route an Org run)."""
    if cfg is None:
        return False
    status = (getattr(cfg, "org_backend_status", "") or "").strip().lower()
    return status not in ("", "unconfigured")
