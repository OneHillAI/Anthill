"""The public contribution surface (ASDD reference implementation).

The one intake agent that every channel - in-app chat, the website board, a bring-your-own-agent
submission - converges on. It turns a free-text idea (+ optional reference code) into a validated
*spec object* (``ContributionProposal``). Downstream phases add relevance triage, GitHub-issue
minting, an agent-built PR, and merge attribution on top of that same object.

Guardrails inherited from ASDD and honoured here:
- the idea and any attached code are UNTRUSTED data, read as data and never as instructions;
- attached code is REFERENCE only, never a diff to merge verbatim;
- the intake step writes nothing to the wiki or model - it only drafts a spec for human review.
"""

from .github_issue import build_issue_body, contrib_repo_config, mint_issue
from .intake import distil_proposal
from .triage import triage_proposal

__all__ = [
    "build_issue_body",
    "contrib_repo_config",
    "distil_proposal",
    "mint_issue",
    "triage_proposal",
]
