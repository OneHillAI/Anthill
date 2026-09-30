from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from ..inference.base import Message
from .providers import PROVIDERS, CloudProvider, CloudResult, call_cloud
from .quality import QualityVerdict, assess
from .scrub import restore, scrub


@dataclass
class HybridPolicy:
    """Per-org rules for when and how to use a paid cloud model.

    Defaults are privacy-maximal: disabled, question-only (no wiki/history sent),
    PII scrubbed. The admin opts in and chooses how much context may leave.
    """

    enabled: bool = False
    provider: str = "openrouter"
    model: str = ""  # "" → provider default
    threshold: float = 0.5  # escalate when local confidence < threshold
    send_context: bool = False  # if True, include wiki context (still scrubbed)
    scrub_pii: bool = True
    api_key: str = ""  # if "", read from the provider's env var
    monthly_budget_usd: float = 0.0  # 0 = no cap

    def resolved_key(self, provider: CloudProvider) -> str:
        return self.api_key or os.environ.get(provider.api_key_env, "")


@dataclass
class EscalationOutcome:
    answer: str
    escalated: bool
    verdict: QualityVerdict
    reason: str = ""
    cloud: CloudResult | None = None
    pii_redacted: int = 0


@dataclass
class EscalationProposal:
    """The DECISION half of escalation (#252): whether a weak local answer *would* be sent to a cloud
    model, plus everything needed to execute that call - but WITHOUT having made it. The outbound
    network call happens only in ``run_escalation``, which a consent boundary must invoke. No consent
    means no cloud call. The private ``_*`` fields carry the (already PII-scrubbed) payload; a consent
    callback reads the public fields to inform the user (provider, model, how much PII was scrubbed)."""

    proposed: bool  # True = a cloud call would run, pending consent
    verdict: QualityVerdict
    reason: str = ""
    provider_name: str = ""  # human-readable, for the consent prompt
    model: str = ""
    pii_redacted: int = 0  # PII spans that would be scrubbed before anything leaves
    _provider: CloudProvider | None = None
    _messages: list[Message] | None = None
    _api_key: str = ""
    _replacements: dict[str, str] = field(default_factory=dict)


def propose_escalation(
    question: str,
    local_answer: str,
    *,
    policy: HybridPolicy,
    context: str = "",
    spent_this_month: float = 0.0,
) -> EscalationProposal:
    """DECISION only: assess the local answer and, if it is weak and every gate (enabled / known
    provider / API key / budget) passes, build the scrubbed outbound payload - but make **no** network
    call. Returns a proposal that ``run_escalation`` can later execute *iff* a consent boundary
    approves it (#252). Question-only by default; PII scrubbed before it is ever put on the wire."""
    verdict = assess(local_answer, question, threshold=policy.threshold)
    if verdict.sufficient or not policy.enabled:
        return EscalationProposal(
            False,
            verdict,
            reason="local answer sufficient" if verdict.sufficient else "hybrid disabled",
        )

    provider = PROVIDERS.get(policy.provider)
    if provider is None:
        return EscalationProposal(False, verdict, reason=f"unknown provider '{policy.provider}'")

    api_key = policy.resolved_key(provider)
    if not api_key:
        return EscalationProposal(False, verdict, reason=f"no API key for {provider.name}")

    if policy.monthly_budget_usd and spent_this_month >= policy.monthly_budget_usd:
        return EscalationProposal(False, verdict, reason="monthly cloud budget reached")

    # Build the minimal outbound payload (question only by default) and scrub PII - but DO NOT send.
    replacements: dict[str, str] = {}
    redacted = 0
    user_text = question
    if policy.send_context and context:
        user_text = f"CONTEXT:\n{context}\n\nQUESTION: {question}"
    if policy.scrub_pii:
        sr = scrub(user_text)
        user_text = sr.text
        redacted = len(sr.replacements)
        replacements = sr.replacements

    messages = [
        Message(
            "system",
            "Answer the user's question accurately and concisely. If you are uncertain, say so.",
        ),
        Message("user", user_text),
    ]
    return EscalationProposal(
        True,
        verdict,
        reason="local below threshold",
        provider_name=provider.name,
        model=policy.model or "",
        pii_redacted=redacted,
        _provider=provider,
        _messages=messages,
        _api_key=api_key,
        _replacements=replacements,
    )


def run_escalation(proposal: EscalationProposal, local_answer: str) -> EscalationOutcome:
    """EXECUTION: perform the *approved* cloud call for a proposal. This is the ONLY place the outbound
    request is made, so it must be reached only through a consent boundary. On any failure it falls
    back to the local answer rather than erroring out."""
    if not (proposal.proposed and proposal._provider and proposal._messages):
        return EscalationOutcome(
            local_answer, False, proposal.verdict, reason="nothing to escalate"
        )
    try:
        cloud = call_cloud(
            proposal._provider,
            proposal._messages,
            api_key=proposal._api_key,
            model=proposal.model or None,
        )
    except Exception as e:
        return EscalationOutcome(
            local_answer, False, proposal.verdict, reason=f"cloud call failed: {e}"
        )
    # Restore any PII placeholders the cloud echoed back, so the redaction is transparent: real values
    # leave scrubbed but come back in the answer the user reads (the map never leaves the machine).
    cloud_answer = restore(cloud.answer, proposal._replacements)
    annotated = (
        f"{cloud_answer}\n\n"
        f"---\n_Answered by {cloud.provider}:{cloud.model} "
        f"(local confidence was {proposal.verdict.confidence:.0%})._"
    )
    return EscalationOutcome(
        answer=annotated,
        escalated=True,
        verdict=proposal.verdict,
        reason="escalated: local below threshold",
        cloud=cloud,
        pii_redacted=proposal.pii_redacted,
    )


def maybe_escalate(
    question: str,
    local_answer: str,
    *,
    policy: HybridPolicy,
    context: str = "",
    spent_this_month: float = 0.0,
    consent: Callable[[EscalationProposal], bool] | None = None,
) -> EscalationOutcome:
    """Decide whether to escalate a weak local answer, then escalate **only with per-use consent** (#252).

    Split into ``propose_escalation`` (decision, no network) + ``run_escalation`` (the cloud call). The
    outbound request is made *iff* ``consent(proposal)`` returns True. With ``consent=None`` (the
    default) the local answer is returned unchanged and **no cloud call is ever made** - fail-closed.
    A consent callback that raises is treated as a refusal."""
    proposal = propose_escalation(
        question, local_answer, policy=policy, context=context, spent_this_month=spent_this_month
    )
    if proposal.proposed and consent is not None:
        try:
            approved = bool(consent(proposal))
        except Exception:
            approved = False
        if approved:
            return run_escalation(proposal, local_answer)
        return EscalationOutcome(
            local_answer, False, proposal.verdict, reason="escalation declined (no consent)"
        )
    return EscalationOutcome(local_answer, False, proposal.verdict, reason=proposal.reason)
