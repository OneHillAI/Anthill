"""#278: shared plumbing for escalating a single turn from the local model to an account's connected
org/RunPod/inference-provider backend - used by the Agents surface (agents_run.py, human-approved) and
the Tasks surface (scheduler.py, bounded by a monthly cap). Chat's OWN per-turn "use the cloud model"
redo (Ask mode) still forces the org plane directly via escalate_org/plane_inference, not this module.

Compound-compute-tiers spec addition: Chat's Automated mode is a DIFFERENT kind of escalation - not to
the account's primary org/cloud plane, but to a separate, orthogonal escalation-provider ATTACHMENT
(OrgSettings.escalation_provider), fired automatically when the lead's own answer looks uncertain. The
trigger decision (should_escalate_automated/grade_answer_locally) and the call-count cap helpers below
are shared with that surface; building the attachment's own backend stays in anthill.web.app (it needs
_INFERENCE_PROVIDERS, which lives there to avoid a circular import).

Deliberately narrow otherwise: this only builds the org-plane backend and logs the audit event. Each
surface still owns its own goal/context/tool assembly - an Agent's approval-flow escalation is a tool-
less one-shot re-answer, while a Task's escalation may want its own real tool set - so that assembly
isn't shared here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from ..agent.intent import CONFIDENCE_CONFIDENT, CONFIDENCE_UNCERTAIN
from ..config import Config
from ..inference.base import ChatResult, InferenceBackend, Message, build_backend
from .audit import log_inference_call
from .plane_routing import PlaneInference, plane_inference


def build_escalation_backend(
    db,
    cfg,
    decrypt: Callable[[str], str],
    *,
    org_id: int | None,
    user_id: int | None,
    surface: str,
) -> tuple[InferenceBackend, PlaneInference]:
    """Force-resolve the org/RunPod/inference-provider plane (same forcing mechanism Chat's redo phrase
    uses) and build a one-off backend for a single escalated call, audited under ``surface``. Raises
    ``PlaneUnavailable`` if nothing is connected anymore - callers should let that propagate to their
    own generic error handling rather than silently falling back to local (an escalation that silently
    becomes a local answer would misrepresent what actually answered the turn)."""
    plane_inf = plane_inference("org", cfg, decrypt=decrypt)
    log_inference_call(db, plane_inf, org_id=org_id, user_id=user_id, surface=surface)
    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = plane_inf.model
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key
    return build_backend(config), plane_inf


_GRADER_PROMPT = (
    "You just answered a question. Rate your own answer honestly.\n\n"
    "Question: {question}\n\nYour answer: {answer}\n\n"
    "Reply with exactly one word: CONFIDENT if the answer is complete and you have no real doubt "
    "about it, or UNSURE if there's a meaningful chance it's incomplete, wrong, or guessed."
)


def grade_answer_locally(local_backend: InferenceBackend, question: str, answer: str) -> bool:
    """The 'ONE local grader call' the compound-compute-tiers spec calls for in Automated mode when
    logprob confidence is unavailable or borderline - a single extra local call (still free relative
    to the paid escalation call it gates) asking the SAME local backend to self-assess before an
    automatic escalation fires. Returns True (escalate) on any ambiguous or unparseable reply, and on
    a backend error - a spurious escalation just costs one paid call, while a false negative here
    would silently ship a wrong answer with no human in the loop to catch it, which is the worse
    failure for an unattended, automatic path."""
    try:
        verdict = local_backend.chat(
            [Message(role="user", content=_GRADER_PROMPT.format(question=question, answer=answer))],
            temperature=0.0,
        )
    except Exception:
        return True
    return "CONFIDENT" not in verdict.upper()


def should_escalate_automated(
    local_backend: InferenceBackend,
    question: str,
    result: ChatResult,
    *,
    confident_threshold: float = CONFIDENCE_CONFIDENT,
    uncertain_threshold: float = CONFIDENCE_UNCERTAIN,
) -> bool:
    """Automated mode's escalation decision (compound-compute-tiers spec): logprob confidence first,
    a local grader call only when that signal is unavailable or falls in the borderline band between
    the two thresholds, before ever spending the paid escalation call. Shares its threshold constants
    with intent.seems_uncertain_for_ask, but is the only one of the two that ever spends the extra
    grader call - Ask mode stays free by design."""
    if result.confidence is not None:
        if result.confidence >= confident_threshold:
            return False
        if result.confidence < uncertain_threshold:
            return True
    return grade_answer_locally(local_backend, question, result.text)


def reset_monthly_escalation_count_if_due(cfg) -> None:
    """Lazily reset the expert-tier attachment's monthly call-count cap (escalation_cap_per_month/
    escalations_this_month/escalations_reset_at) - mirrors scheduler.py's task-specific
    _reset_monthly_escalation_count_if_due (reset on next use, same as cloud_spent_usd elsewhere)."""
    reset_at = getattr(cfg, "escalations_reset_at", None)
    now = datetime.now(timezone.utc)
    if reset_at is None or (now - reset_at.replace(tzinfo=timezone.utc)) > timedelta(days=30):
        cfg.escalations_this_month = 0
        cfg.escalations_reset_at = now


def escalation_cap_reached(cfg) -> bool:
    """Resets the counter first if a month has passed, then checks it against the configured cap -
    callers that go on to actually escalate must still call record_escalation_used() themselves."""
    reset_monthly_escalation_count_if_due(cfg)
    cap = int(getattr(cfg, "escalation_cap_per_month", 20) or 20)
    used = int(getattr(cfg, "escalations_this_month", 0) or 0)
    return used >= cap


def record_escalation_used(cfg) -> None:
    cfg.escalations_this_month = int(getattr(cfg, "escalations_this_month", 0) or 0) + 1
