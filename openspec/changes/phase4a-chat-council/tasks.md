# Tasks

- [ ] Change `run_council()`'s signature in `anthill/council/engine.py` from `(cfg, question: str,
  decrypt, *, system: str | None = None, temperature: float = 0.2)` to `(cfg, messages:
  Sequence[Message], decrypt, *, temperature: float = 0.2)`. Remove `_propose_messages` (members are
  called with `messages` directly via `m.backend.chat(messages, temperature=temperature)`). Rewrite
  `_synthesis_messages(messages: Sequence[Message], proposals: list[tuple[int, str]]) -> list[Message]`
  to build the synthesis prompt from the passed-in `messages` (e.g. derive the displayed "original
  question" from the last `role="user"` message) instead of a separate `question: str` parameter.
- [ ] Update `tests/test_council_engine.py` to the new signature. Every existing test's INTENT must
  still be proven (see the reference file below for what each test proves) - just change how the
  question is supplied (a `list[Message]` instead of a bare string).
- [ ] Add optional `cfg=None, decrypt=None` parameters to `ask()` in `anthill/wiki/ask.py`. At the
  existing single-backend call site (the plain-text branch), try the council first when eligible, fall
  back to today's single call otherwise.
- [ ] Add the same optional `cfg=None, decrypt=None` parameters to `ask_stream()`. Widen its existing
  injection-suspect delegation-to-`ask()` branch to also trigger when the council is active, passing
  `cfg`/`decrypt` through.
- [ ] Update `anthill/web/app.py`'s `chat_stream` route to pass `cfg` and `_safe_decrypt` through to both
  its `ask_stream()` and `ask()` call sites.
- [ ] New tests proving the acceptance criteria in proposal.md (fake backends, no live account/network).

## Reference: current `anthill/council/engine.py` (Phase 3, merged, VERBATIM - the exact code to modify)

```python
"""Mixture-of-Agents engine: resolve council members -> parallel propose -> synthesis. ..."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Config
from ..inference.base import InferenceBackend, Message, build_backend

log = logging.getLogger(__name__)

_MEMBER_TIMEOUT_S = 130.0
_SYNTHESIS_TIMEOUT_S = 180.0
_MAX_PARALLEL = 8

# ... _members_from_cfg, ResolvedMember, _provider_backend_kind, resolve_council_backends: UNCHANGED,
# do not touch. Only the prompt-building and run_council()'s signature change.

def _propose_messages(question: str, system: str | None) -> list[Message]:
    msgs: list[Message] = []
    if system:
        msgs.append(Message(role="system", content=system))
    msgs.append(Message(role="user", content=question))
    return msgs

_SYNTHESIS_SYSTEM = (
    "You are the lead of a small council of AI models. Several council members independently drafted "
    "answers to the same question. Your job is to produce ONE final answer for the user by "
    "reconciling the drafts: keep what they agree on, resolve contradictions using your own judgement, "
    "correct clear errors, and drop redundancy. Do not mention the council, the drafts, or that "
    "multiple models were involved - return only the final answer, as if you had written it directly."
)

def _synthesis_messages(question: str, proposals: list[tuple[int, str]], system: str | None) -> list[Message]:
    parts = [f"Original question:\n{question}\n", "Council member drafts:"]
    for n, (_idx, text) in enumerate(proposals, start=1):
        parts.append(f"\n--- Draft {n} ---\n{text.strip()}")
    parts.append("\nNow write the single best final answer.")
    body = "\n".join(parts)
    sys = _SYNTHESIS_SYSTEM if not system else f"{system}\n\n{_SYNTHESIS_SYSTEM}"
    return [Message(role="system", content=sys), Message(role="user", content=body)]

class AllMembersFailed(RuntimeError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("all council members failed: " + "; ".join(errors))

class NoMembersResolved(RuntimeError): ...

@dataclass
class CouncilResult:
    answer: str
    proposer_indexes: list[int] = field(default_factory=list)
    failures: list[tuple[int, str]] = field(default_factory=list)
    synthesized: bool = False

def run_council(
    cfg, question: str, decrypt: Callable[[str], str], *, system: str | None = None,
    temperature: float = 0.2,
) -> CouncilResult:
    resolved = resolve_council_backends(cfg, decrypt)
    if not resolved:
        raise NoMembersResolved("no council members resolvable")

    if len(resolved) == 1:
        m = resolved[0]
        answer = m.backend.chat(_propose_messages(question, system), temperature=temperature)
        return CouncilResult(answer=answer, proposer_indexes=[m.index], synthesized=False)

    _memory_pressure_note(resolved)

    proposals: list[tuple[int, str]] = []
    failures: list[tuple[int, str]] = []
    max_workers = min(len(resolved), _MAX_PARALLEL)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_member = {
            ex.submit(m.backend.chat, _propose_messages(question, system), temperature=temperature): m
            for m in resolved
        }
        done, not_done = concurrent.futures.wait(future_to_member, timeout=_MEMBER_TIMEOUT_S)
        # ... (collect proposals/failures from done, mark not_done as timed-out - UNCHANGED)
    finally:
        ex.shutdown(wait=False)

    if not proposals:
        raise AllMembersFailed([f"member {i}: {msg}" for i, msg in failures])

    proposals.sort(key=lambda p: p[0])
    proposer_indexes = [i for i, _ in proposals]
    if len(proposals) == 1:
        return CouncilResult(answer=proposals[0][1], proposer_indexes=proposer_indexes,
                              failures=failures, synthesized=False)

    failed_indexes = {i for i, _ in failures}
    lead = next((m for m in resolved if m.index == 0), None)
    if lead is None or lead.index in failed_indexes:
        surviving = {i for i, _ in proposals}
        lead = min((m for m in resolved if m.index in surviving), key=lambda m: m.index)

    synth_ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = synth_ex.submit(lead.backend.chat, _synthesis_messages(question, proposals, system),
                               temperature=temperature)
        answer = fut.result(timeout=_SYNTHESIS_TIMEOUT_S)
    except Exception as e:
        return CouncilResult(answer=proposals[0][1], proposer_indexes=proposer_indexes,
                              failures=[*failures, (lead.index, f"synthesis: {e}")], synthesized=False)
    finally:
        synth_ex.shutdown(wait=False)

    return CouncilResult(answer=answer, proposer_indexes=proposer_indexes, failures=failures, synthesized=True)
```

Only `_propose_messages`, `_synthesis_messages`, and `run_council`'s signature/body change. Everything
else (`resolve_council_backends`, `ResolvedMember`, `_provider_backend_kind`, `_members_from_cfg`,
`_memory_pressure_note`, the timeout/executor-shutdown handling) is unchanged - reuse as-is.

## Reference: `ask()`'s exact single-backend call site to modify (`anthill/wiki/ask.py:337-429`, verbatim)

```python
    if answer is None:
        if has_img and images_b64:
            messages = [ ... ]
            answer = _chat(backend, messages, model_override, num_predict=ANSWER_MAX_TOKENS)
        else:
            messages = prompts.answer_question(context, question, history=prepared_history)
            suspect = has_injection_imperative(question) or has_injection_imperative(context)
            first_kwargs: dict = {"num_predict": ANSWER_MAX_TOKENS}
            if suspect:
                first_kwargs["think"] = False
                if router is not None:
                    try:
                        from ..routing.router import TaskType
                        model_override = router.pick(TaskType.GENERAL)
                    except Exception:
                        pass
            try:
                answer = _chat(backend, messages, model_override, **first_kwargs)     # <-- THE call site
            except BackendError as e:
                if "empty response" not in str(e).lower():
                    raise
                answer = ""
            if _looks_hijacked(answer, question, context) or not answer.strip():
                messages = prompts.answer_question(context, question, history=prepared_history, reassert=True)
                try:
                    retry = _chat(backend, messages, model_override, num_predict=ANSWER_MAX_TOKENS, think=False)
                except Exception:
                    retry = ""
                retry_usable = retry.strip() and not _looks_hijacked(retry, question, context)
                answer = (retry.strip() if retry_usable else "") or (... safe refusal strings ...)
```

The `if has_img and images_b64:` branch is OUT OF SCOPE (leave it calling `_chat` directly, never the
council). Only the `else:` branch's marked call site is eligible for council routing - and only on the
FIRST attempt, not the hijack-retry (the retry is a security-hardening path with `think=False` forced
and a capable-model override already applied; keep it on the single backend to avoid re-running an
expensive multi-model council call on what is already an exceptional, rare path - a design call, but a
deliberate one: state it explicitly in the PR if you choose differently).

## Reference: `ask_stream()`'s exact delegation branch to widen (`anthill/wiki/ask.py:502-523`, verbatim)

```python
    if has_injection_imperative(question) or has_injection_imperative(context):
        answer, _slugs, _hit = ask(
            ws, question, backend, k=k, history=history, profile=profile, principles=principles,
            memory_context=memory_context, extra_workspaces=extra_workspaces, router=router,
            shared_cache=shared_cache,
        )
        yield answer
        return
```
Widen the `if` condition to also delegate when the council is active (2+ resolvable members), and pass
`cfg=cfg, decrypt=decrypt` through to the delegated `ask(...)` call so it can actually route to the
council. Resolve the council membership ONCE (don't call `resolve_council_backends` twice - once here to
decide whether to delegate, and the caller doesn't need to re-resolve inside the delegated `ask()` call
if you thread the result through, though re-resolving inside `ask()` is also acceptable since it is
cheap - a design call for whichever is cleaner).

## Reference: `anthill/web/app.py`'s two call sites to update (`app.py:8613-8656`, verbatim)

```python
                elif can_stream:
                    grabbed: list = []
                    for token in ask_stream(
                        ws, effective_message, backend, history=history, profile=profile_used,
                        principles=principles, memory_context=memory_context, extra_workspaces=extra_ws,
                        shared_cache=org_cache_shared, router=router, on_context=grabbed.extend,
                        # <-- ADD: cfg=cfg, decrypt=_safe_decrypt
                    ):
                        full_response.append(token)
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    slugs = grabbed
                else:
                    answer, slugs, cache_hit = ask(
                        ws, effective_message, backend, web_search=web_effective, images_b64=images_b64,
                        memory_context=memory_context, profile=profile_used, principles=principles,
                        extra_workspaces=extra_ws, shared_cache=org_cache_shared, router=router,
                        org_url=org_url, hybrid_policy=policy, spent_this_month=spent,
                        on_escalation=_track, history=history, search_query=search_query,
                        cache_threshold=cache_thr,
                        # <-- ADD: cfg=cfg, decrypt=_safe_decrypt
                    )
```
`cfg` and `_safe_decrypt` are already in scope at both call sites (verified: `_safe_decrypt` is defined
at `app.py:8066`; `cfg` is used a few lines below the `ask()` call already, at `if escalations and
cfg:`). This is purely additive - no other change needed in this route.

## Reference: `InferenceBackend` Protocol (`anthill/inference/base.py`, verbatim, unchanged by this work)

```python
@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[str] | None = None

@runtime_checkable
class InferenceBackend(Protocol):
    model: str
    def chat(self, messages: Sequence[Message], *, temperature: float = 0.2) -> str: ...
    def health(self) -> str | None: ...
```
Council members are only ever called via `.chat()` (this Protocol's only generation method) - no
`chat_stream`, no `chat_with_tools`, no images. This is consistent with Phase 4a's scope: plain text
only, image questions explicitly excluded (see proposal.md).
