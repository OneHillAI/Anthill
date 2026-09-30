"""Channel-agnostic interaction routing for the members interaction service (Stage 1).

A member's message is either a QUESTION (answer from the org wiki) or an IDEA / bug / request (route it
into the governed contribution intake as a spec object). This module holds the pure, platform-neutral
pieces the ASDD interaction agent needs on every surface - the classifier, the automated-agent
disclosure, and the reply wording - so Slack, Discord, and the web widget share one behaviour. The
side-effectful parts (the wiki answer and the intake write) stay with the caller, so this module has no
web or db imports and is unit-testable on its own.

Stage 1 is members-only, so the identity is trusted (the Slack bot already gates to org members) and the
agent may use the org model + wiki. Untrusted public surfaces are Stage 2 and run execution-free.
"""

from __future__ import annotations

# Said on every reply: the interaction agent always discloses it is automated and under human direction.
DISCLOSURE = "Automated ASDD assistant, under human direction."

# Phrases that mark a message as a feature idea / bug / request to route into intake rather than a
# question to answer. Kept explicit and conservative on purpose (see looks_like_idea).
_IDEA_MARKERS = (
    "feature request",
    "feature idea",
    "feature:",
    "idea:",
    "suggestion:",
    "proposal:",
    "request:",
    "bug:",
    "bug report",
    "it would be great if",
    "it would be nice",
    "would be great if",
    "can you add",
    "could you add",
    "please add",
    "we should add",
    "we should support",
    "i wish",
    "would love to see",
)


def looks_like_idea(text: str) -> bool:
    """True if the message reads as an idea / bug / request to route into intake, not a question to
    answer. Conservative: an ambiguous message is treated as a question, because a mis-answered request
    only gets an unwanted answer, while a mis-routed question drafts a confusing spec. A message that is
    clearly a question ("how do I ...", ends in "?") is never treated as an idea."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if t.endswith("?") or t.startswith(
        ("how ", "what ", "why ", "when ", "where ", "who ", "which ")
    ):
        return False
    return any(m in t for m in _IDEA_MARKERS)


def intake_reply(*, title: str, ready: bool, needs_detail: str = "") -> str:
    """The reply after routing an idea into the contribution intake. `ready` is the definition-of-ready
    verdict (the proposal's completeness). Always discloses; always makes clear a human decides."""
    title = (title or "your idea").strip()
    if ready:
        body = (
            f"I have drafted this as a spec and routed it into the contribution intake for a "
            f"maintainer to review:\n> {title}\nIt is ready for review. Nothing is built without a "
            f"human approving it."
        )
    else:
        need = (needs_detail or "").strip() or (
            "a bit more on the problem it solves and what a finished version looks like"
        )
        body = (
            f'I have started a draft spec for "{title}", but it needs more before it can enter '
            f"intake: {need}. Reply with that and I will route it."
        )
    return f"{DISCLOSURE}\n\n{body}"


def answer_reply(answer: str) -> str:
    """Prefix a wiki-grounded answer with the automated-agent disclosure."""
    return f"{DISCLOSURE}\n\n{(answer or '').strip()}"
