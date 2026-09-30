"""Mixture-of-Agents (MoA) council orchestration engine.

Standalone engine only. NOT wired into ask.py/scheduler.py/agents_run.py (that is Phase 4). See
openspec proposal 'Phase 3'.
"""

from .engine import (
    AllMembersFailed,
    CouncilResult,
    NoMembersResolved,
    ResolvedMember,
    resolve_council_backends,
    run_council,
)

__all__ = [
    "AllMembersFailed",
    "CouncilResult",
    "NoMembersResolved",
    "ResolvedMember",
    "resolve_council_backends",
    "run_council",
]
