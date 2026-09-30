"""Mixture-of-Agents engine: resolve council members -> parallel propose -> synthesis.

This module is deliberately decoupled from anthill.web.app: it does NOT import _members_from_cfg from
app.py (that module is ~11k lines and will import THIS module in Phase 4, so importing it here risks a
circular import). Instead it duplicates the tiny member-parsing logic (a JSON parse + default merge)
locally - short and stable enough that the duplication is cheaper than introducing a shared low-level
module solely for it.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..config import Config
from ..inference.base import InferenceBackend, Message, build_backend

log = logging.getLogger(__name__)

# --- Tunables (open-question answers, stated explicitly) -------------------------------------------

# Bounds the WHOLE propose layer's wait, not each member individually - every member starts at
# roughly the same time (the fan-out below), so one shared clock is the correct model, not N
# independent per-member clocks. Must stay a little ABOVE Config.timeout's own socket timeout (300s,
# PR #661 Tier 0 - a cold rented model can take that long to spin up), so a member whose own timeout
# fires promptly is reported as a normal chat failure rather than racing this outer bound. This was
# 130s when Config.timeout was 120s; it silently fell out of sync when the socket timeout moved to
# 300s, cutting a genuinely-cold proposer off well before its own timeout could ever fire.
_MEMBER_TIMEOUT_S = 310.0

# The synthesis call gets a bit more headroom - it is a single call on the lead and its input (all
# proposals) can be large.
_SYNTHESIS_TIMEOUT_S = 180.0

# Cap fan-out concurrency so a large council can't spawn unbounded threads.
_MAX_PARALLEL = 8


# --- Member resolution -------------------------------------------------------------------------------

# Duplicated (intentionally, see module docstring) from app.py's _empty_member(); kept minimal - only
# the fields this module actually reads.
_MEMBER_DEFAULTS = {
    "endpoint": "",
    "provider": "",
    "model": "",
    "model_key_enc": "",
    "backend_status": "unconfigured",
    "backend_handle": "",
    "lifecycle": "vpc",
}


def _members_from_cfg(cfg) -> list[dict]:
    """Parse cfg.org_council_members (JSON) into a list of member dicts, merged onto the defaults.

    Local duplicate of app.py's _members_from_cfg to avoid a circular import (Phase 4 has app.py
    import THIS module). Returns [] on any parse failure or absent config.
    """
    raw = getattr(cfg, "org_council_members", "") or ""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        merged = dict(_MEMBER_DEFAULTS)
        merged.update(item)
        out.append(merged)
    return out


@dataclass
class ResolvedMember:
    """A council member that has been turned into a live backend."""

    index: int
    backend: InferenceBackend
    provider: str
    is_local: bool  # provider == "onprem"


def _provider_backend_kind(provider: str) -> str:
    """Map a member's provider to an InferenceBackend backend kind.

    on-prem members run Ollama; every hosted VPC provider (lambda/runpod/datacrunch/...) exposes an
    OpenAI-compatible endpoint. Mirrors how plane_inference() picks "ollama" vs "openai" for the
    legacy single member.
    """
    if (provider or "").strip().lower() == "onprem":
        return "ollama"
    return "openai"


def resolve_council_backends(cfg, decrypt: Callable[[str], str]) -> list[ResolvedMember]:
    """Turn cfg.org_council_members into a list of live InferenceBackends.

    Only members with lifecycle == "vpc" AND a non-blank endpoint AND a non-blank model are
    resolvable today. "inference-provider" members (Phase 5) and unconfigured members are skipped
    silently (not errors).

    Order is preserved: index 0 is the lead/core model (product convention). Builds each backend via
    build_backend(Config(...)) - the same convention every existing surface uses (_backend_for in
    agents_run.py, _run_task in scheduler.py, _backend_from_cfg in app.py).
    """
    members = _members_from_cfg(cfg)
    resolved: list[ResolvedMember] = []
    for idx, m in enumerate(members):
        lifecycle = (m.get("lifecycle", "") or "vpc").strip().lower()
        if lifecycle != "vpc":
            log.debug("council member %d skipped: lifecycle=%r not resolvable", idx, lifecycle)
            continue
        endpoint = (m.get("endpoint", "") or "").strip()
        model = (m.get("model", "") or "").strip()
        if not endpoint or not model:
            log.debug("council member %d skipped: blank endpoint/model", idx)
            continue

        provider = (m.get("provider", "") or "").strip().lower()
        api_key: str | None = None
        key_enc = (m.get("model_key_enc", "") or "").strip()
        if key_enc:
            try:
                api_key = decrypt(key_enc)
            except Exception:
                # A member whose key won't decrypt is treated as unresolvable rather than built with
                # a bad/None key that would 401 later.
                log.warning("council member %d skipped: model_key_enc failed to decrypt", idx)
                continue

        config = Config.from_env()
        config.backend = _provider_backend_kind(provider)
        config.base_url = endpoint
        config.model = model
        if api_key:
            config.api_key = api_key

        try:
            backend = build_backend(config)
        except Exception:
            log.warning("council member %d skipped: build_backend failed", idx, exc_info=True)
            continue

        resolved.append(
            ResolvedMember(
                index=idx, backend=backend, provider=provider, is_local=(provider == "onprem")
            )
        )
    return resolved


# --- Prompts (open-question: prompt engineering is a design call, stated explicitly) -----------------
#
# The propose layer just answers the user's question directly - no council awareness, so a
# single-member degrade path and a proposer produce identical prompts and identical answers
# (important: a 1-member council must be behaviourally identical to no council at all). The synthesis
# layer is the ONLY place council-awareness appears.


_SYNTHESIS_SYSTEM = (
    "You are the lead of a small council of AI models. Several council members independently drafted "
    "answers to the same question. Your job is to produce ONE final answer for the user by "
    "reconciling the drafts: keep what they agree on, resolve contradictions using your own judgement, "
    "correct clear errors, and drop redundancy. Do not mention the council, the drafts, or that "
    "multiple models were involved - return only the final answer, as if you had written it directly."
)


def _last_user_text(messages: Sequence[Message]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content
    return ""


def _synthesis_messages(
    messages: Sequence[Message], proposals: list[tuple[int, str]]
) -> list[Message]:
    question = _last_user_text(messages)
    system = next((m.content for m in messages if m.role == "system"), None)
    parts = [f"Original question:\n{question}\n", "Council member drafts:"]
    for n, (_idx, text) in enumerate(proposals, start=1):
        parts.append(f"\n--- Draft {n} ---\n{text.strip()}")
    parts.append("\nNow write the single best final answer.")
    body = "\n".join(parts)
    # The lead's own domain system prompt (if any) is preserved, then the synthesis instruction is
    # appended so the lead keeps its persona.
    sys = _SYNTHESIS_SYSTEM if not system else f"{system}\n\n{_SYNTHESIS_SYSTEM}"
    return [Message(role="system", content=sys), Message(role="user", content=body)]


# --- Result / error types -----------------------------------------------------------------------------


class AllMembersFailed(RuntimeError):
    """Raised when EVERY council member failed in the propose layer.

    Distinguishable from a normal exception so callers (Phase 4) can surface a clear "council
    unavailable" error rather than a generic failure.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("all council members failed: " + "; ".join(errors))


class NoMembersResolved(RuntimeError):
    """No member resolved at all (empty/unconfigured council)."""


@dataclass
class CouncilResult:
    answer: str
    proposer_indexes: list[int] = field(default_factory=list)  # members whose proposal survived
    failures: list[tuple[int, str]] = field(default_factory=list)  # (index, error) for the rest
    synthesized: bool = False  # False => a single member's answer was returned directly
    # Each surviving member's own draft text, in council order - previously computed and then
    # discarded once synthesis ran. Kept so callers can bank the full reasoning process (not just the
    # flattened final answer) as training data. [] when synthesized is False (nothing to bank beyond
    # the single answer already returned).
    proposals: list[tuple[int, str]] = field(default_factory=list)


# --- The engine ----------------------------------------------------------------------------------------


def _memory_pressure_note(resolved: list[ResolvedMember]) -> str | None:
    """Reuse verify.py's free_mem_gb() guard for the multi-ON-PREM case only.

    VPC/hosted members each run on their own dedicated cloud instance, so concurrent inference across
    them is NOT a local-memory concern (the guard would be meaningless - free_mem_gb() measures THIS
    host, not theirs). The one case where concurrent inference DOES risk local memory pressure is 2+
    on-prem members running on this same host.

    This DETECTS and LOGS that case but deliberately does NOT block: unlike verify.py (which chooses
    whether to LOAD a second model), on-prem council members are assumed already loaded/resident, and
    silently degrading a configured council to a partial one is worse than the risk. This is a KNOWN,
    NAMED gap: hard back-pressure for co-located multi-on-prem inference is not implemented in this
    phase; it is surfaced as a log warning so it stays observable rather than silently accepted.
    """
    local = [m for m in resolved if m.is_local]
    if len(local) < 2:
        return None
    try:
        from ..hosting.sizing import free_mem_gb

        free = free_mem_gb()
    except Exception:
        return None
    if free is not None and free < 6.0:
        note = (
            f"council has {len(local)} on-prem members and only {free:.1f} GB free; concurrent local "
            "inference may face memory pressure (known gap: not gated)"
        )
        log.warning(note)
        return note
    return None


def run_council(
    cfg,
    messages: Sequence[Message],
    decrypt: Callable[[str], str],
    *,
    temperature: float = 0.2,
) -> CouncilResult:
    """Run the account's council on `messages` (a full conversation - system/history/user, exactly the
    shape every other call site in this codebase already builds) and return one final answer.

    - 0 members resolve  -> NoMembersResolved
    - 1 member resolves  -> direct chat() (no fan-out, no synthesis)
    - 2+ members resolve -> parallel propose -> synthesis
    - a member failing in the propose layer is tolerated as long as >=1 proposer succeeds
    - every member failing -> AllMembersFailed
    """
    resolved = resolve_council_backends(cfg, decrypt)
    if not resolved:
        raise NoMembersResolved("no council members resolvable")

    msgs = list(messages)

    if len(resolved) == 1:
        m = resolved[0]
        answer = m.backend.chat(msgs, temperature=temperature)
        return CouncilResult(answer=answer, proposer_indexes=[m.index], synthesized=False)

    _memory_pressure_note(resolved)  # log-only; see docstring

    proposals: list[tuple[int, str]] = []
    failures: list[tuple[int, str]] = []

    max_workers = min(len(resolved), _MAX_PARALLEL)
    # Deliberately NOT a "with ThreadPoolExecutor(...) as ex:" block: ThreadPoolExecutor.__exit__
    # calls shutdown(wait=True), which blocks until every submitted thread finishes - including any
    # that concurrent.futures.wait() below already gave up on. That would silently re-introduce the
    # exact bug this function's timeout handling is fixing (a hung member blocking the whole call).
    # Python cannot forcibly kill a running thread, so a hung member's thread is left to finish (or
    # not) in the background; shutdown(wait=False) only stops accepting new work, it does not block.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_member = {
            ex.submit(m.backend.chat, msgs, temperature=temperature): m for m in resolved
        }
        # concurrent.futures.wait(..., timeout=X) bounds the WHOLE wait to X seconds and partitions
        # futures into done/not_done - unlike as_completed(), which only ever yields an ALREADY-
        # finished future, so wrapping its output in .result(timeout=...) enforces nothing (the
        # underlying call has already run to completion by the time as_completed hands it back).
        done, not_done = concurrent.futures.wait(future_to_member, timeout=_MEMBER_TIMEOUT_S)
        for fut in done:
            m = future_to_member[fut]
            try:
                text = fut.result()
            except Exception as e:  # network, backend error - NOT a timeout, that's handled below
                failures.append((m.index, f"{type(e).__name__}: {e}"))
                log.warning("council member %d failed in propose layer: %s", m.index, e)
                continue
            if text and text.strip():
                proposals.append((m.index, text))
            else:
                failures.append((m.index, "empty response"))
        for fut in not_done:
            m = future_to_member[fut]
            failures.append((m.index, f"timed out after {_MEMBER_TIMEOUT_S:.0f}s"))
            log.warning("council member %d timed out in propose layer", m.index)
            fut.cancel()  # best-effort: cannot interrupt an already-running thread, only a queued one
    finally:
        ex.shutdown(wait=False)

    if not proposals:
        raise AllMembersFailed([f"member {i}: {msg}" for i, msg in failures])

    proposals.sort(key=lambda p: p[0])  # preserve council order in what synthesis sees
    proposer_indexes = [i for i, _ in proposals]

    if len(proposals) == 1:
        # Synthesising a single draft is a no-op paraphrase at best - return it directly.
        return CouncilResult(
            answer=proposals[0][1],
            proposer_indexes=proposer_indexes,
            failures=failures,
            synthesized=False,
        )

    # One synthesis call on the lead (index 0, product convention). If the lead itself failed in the
    # propose layer, fall back to the lowest-index surviving proposer as synthesiser.
    failed_indexes = {i for i, _ in failures}
    lead = next((m for m in resolved if m.index == 0), None)
    if lead is None or lead.index in failed_indexes:
        surviving = {i for i, _ in proposals}
        lead = min((m for m in resolved if m.index in surviving), key=lambda m: m.index)

    # Same reasoning as the propose layer: not a "with ... as ex:" block, so a synthesis timeout
    # doesn't get masked by __exit__'s shutdown(wait=True) blocking on the still-running thread.
    synth_ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = synth_ex.submit(
            lead.backend.chat,
            _synthesis_messages(msgs, proposals),
            temperature=temperature,
        )
        answer = fut.result(timeout=_SYNTHESIS_TIMEOUT_S)
    except Exception as e:
        # Synthesis failed (or timed out) but we have valid proposals: degrade to the
        # highest-priority (lowest-index) surviving proposal rather than failing the whole answer.
        log.warning("council synthesis failed (%s); falling back to top proposal", e)
        return CouncilResult(
            answer=proposals[0][1],
            proposer_indexes=proposer_indexes,
            failures=[*failures, (lead.index, f"synthesis: {e}")],
            synthesized=False,
        )
    finally:
        synth_ex.shutdown(wait=False)

    return CouncilResult(
        answer=answer,
        proposer_indexes=proposer_indexes,
        failures=failures,
        synthesized=True,
        proposals=proposals,
    )


# --- Phase 4b: review a completed answer (scheduler/agent-run surfaces) ------------------------------
#
# Unlike run_council() (parallel independent drafting for chat), a scheduled task or agent run executes
# its tool-calling loop on ONE model only - re-running that loop per council member would risk each
# member independently firing the same side-effecting tool call. So here, the lead has ALREADY produced
# `draft_answer` by the time this is called; reviewers only ever critique that text via .chat() - they
# are never given tool access, and the tool-calling loop never runs more than once.

_REVIEW_TIMEOUT_S = (
    60.0  # a critique is a short text response, not a multi-step tool loop - a smaller
)
# bound than run_council()'s _MEMBER_TIMEOUT_S (130s) is appropriate, but still generous.
_REVISION_TIMEOUT_S = (
    90.0  # the lead's revision call sees every surviving critique at once - more input
)
# than a single critique, a bit more headroom than _REVIEW_TIMEOUT_S.

_REVIEWER_SYSTEM = (
    "You are reviewing a completed answer to a task, produced by another AI. Critique it: point out "
    "factual errors, missed requirements, or ways it could be improved. Be specific and concise. You "
    "are reviewing text only - you have no ability to take any action yourself."
)

_REVISION_SYSTEM = (
    "You already produced a draft answer to the task below. Other reviewers have critiqued it. Decide "
    "whether their critiques are valid, then output your final answer - either your original draft "
    "unchanged, or a revision addressing valid points. Do not mention the review process or the "
    "reviewers - return only the final answer, as if you had written it directly."
)


def _reviewer_messages(goal: str, draft_answer: str) -> list[Message]:
    return [
        Message(role="system", content=_REVIEWER_SYSTEM),
        Message(
            role="user", content=f"Task:\n{goal}\n\nCompleted answer to review:\n{draft_answer}"
        ),
    ]


def _revision_messages(
    goal: str, draft_answer: str, critiques: list[tuple[int, str]]
) -> list[Message]:
    parts = [f"Task:\n{goal}\n", f"Your draft answer:\n{draft_answer}\n", "Reviewer critiques:"]
    for n, (_idx, text) in enumerate(critiques, start=1):
        parts.append(f"\n--- Reviewer {n} ---\n{text.strip()}")
    parts.append("\nNow output your final answer.")
    return [
        Message(role="system", content=_REVISION_SYSTEM),
        Message(role="user", content="\n".join(parts)),
    ]


@dataclass
class ReviewResult:
    answer: str
    reviewed: bool = False  # False => 0 reviewers were resolvable; answer is the draft, unchanged
    revised: bool = False  # True => the lead's revision call actually ran and produced this answer
    failures: list[tuple[int, str]] = field(default_factory=list)


def review_completed_answer(
    cfg,
    goal: str,
    draft_answer: str,
    lead_backend: InferenceBackend,
    decrypt: Callable[[str], str],
    *,
    temperature: float = 0.2,
) -> ReviewResult:
    """Have the account's council reviewers (index != 0) critique an already-completed answer, then have
    the SAME lead backend that produced it fold their critiques into a possibly-revised final answer.

    Reviewers are given ONLY .chat() - never .chat_with_tools(), never a Tool instance. The tool-calling
    loop that produced `draft_answer` already ran exactly once, on `lead_backend`, before this is ever
    called; this function never re-runs it and never lets a reviewer take any action.

    Degrades to `draft_answer` unchanged whenever review can't add value: 0 reviewers resolvable, every
    reviewer failing, or the revision call itself failing - a review can only improve the answer or be a
    no-op, never break or worsen it.
    """
    resolved = resolve_council_backends(cfg, decrypt)
    reviewers = [m for m in resolved if m.index != 0]
    if not reviewers:
        return ReviewResult(answer=draft_answer, reviewed=False)

    messages = _reviewer_messages(goal, draft_answer)
    critiques: list[tuple[int, str]] = []
    failures: list[tuple[int, str]] = []

    max_workers = min(len(reviewers), _MAX_PARALLEL)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_member = {
            ex.submit(m.backend.chat, messages, temperature=temperature): m for m in reviewers
        }
        done, not_done = concurrent.futures.wait(future_to_member, timeout=_REVIEW_TIMEOUT_S)
        for fut in done:
            m = future_to_member[fut]
            try:
                text = (
                    fut.result()
                )  # a plain str, per InferenceBackend.chat()'s contract - not an object
            except Exception as e:
                failures.append((m.index, f"{type(e).__name__}: {e}"))
                continue
            if text and text.strip():
                critiques.append((m.index, text))
            else:
                failures.append((m.index, "empty critique"))
        for fut in not_done:
            m = future_to_member[fut]
            failures.append((m.index, f"timed out after {_REVIEW_TIMEOUT_S:.0f}s"))
            fut.cancel()
    finally:
        ex.shutdown(wait=False)

    if not critiques:
        return ReviewResult(answer=draft_answer, reviewed=True, revised=False, failures=failures)

    critiques.sort(key=lambda c: c[0])
    revision_ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = revision_ex.submit(
            lead_backend.chat,
            _revision_messages(goal, draft_answer, critiques),
            temperature=temperature,
        )
        revised_answer = fut.result(timeout=_REVISION_TIMEOUT_S)
    except Exception as e:
        return ReviewResult(
            answer=draft_answer,
            reviewed=True,
            revised=False,
            failures=[*failures, (-1, f"revision: {e}")],
        )
    finally:
        revision_ex.shutdown(wait=False)

    if not revised_answer or not revised_answer.strip():
        return ReviewResult(
            answer=draft_answer,
            reviewed=True,
            revised=False,
            failures=[*failures, (-1, "revision: empty response")],
        )
    return ReviewResult(answer=revised_answer, reviewed=True, revised=True, failures=failures)
