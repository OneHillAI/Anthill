# Phase 4b: council review for scheduled tasks and agent runs

## Why

Phase 4a (merged, PR #660) wires the council into plain chat, where "ask N models the same question,
synthesize" is safe because chat never calls tools. This change covers the other two surfaces named in
`docs/specs/product-council-architecture.md` R4: scheduled tasks (`anthill/web/scheduler.py`) and agent
runs (`anthill/web/agents_run.py`). Both drive `AgentExecutor.run()` - a ReAct tool-calling loop where a
step can send an email, write a file, or call an external API. Running that whole loop once per council
member and combining the text answers afterward would risk each member independently deciding to fire
the same side-effecting tool call.

**Explicit design decision (confirmed with the human maintainer directly, not inferred): one model runs
the actual task, end to end, exactly as today. If the account has council reviewers configured, they
review the FINISHED answer afterward - in parallel, text only, no tool access - and the lead folds their
critiques into a possibly-revised final answer. No duplicate execution, no duplicate side effects.**

## What already exists (verified against `origin/main` @ 730f060, which includes Phase 4a - so
`anthill/council/engine.py`'s `run_council()` takes `messages: Sequence[Message]`, not a bare `question:
str`. Reuse its concurrency pattern AND its new messages-based signature.)

### `anthill/web/scheduler.py`'s `_run_task` (verbatim, single blocking call, no streaming)

```python
def _run_task(task: ScheduledTask, db) -> str:
    ...
    from ..agent.executor import AgentExecutor
    from ..agent.tools import files_owner, make_tools
    from ..config import Config
    from ..inference.base import build_backend
    from .crypto import decrypt
    from .plane_routing import plane_inference

    cfg_row = db.query(OrgSettings).filter(OrgSettings.org_id == task.org_id).first()
    plane_inf = plane_inference(getattr(task, "plane", "solo"), cfg_row, decrypt=decrypt)
    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = plane_inf.model
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key
    ...
    backend = build_backend(config)
    ...
    result = executor.run(goal, context=ctx)
    return result.answer
```

Caller (tick loop), verbatim:
```python
            result = _run_task(task, db) or ""
            task.status = "done"
            task.last_result = result[:4000]
            _verify_task_result(task, result, db)  # advisory cross-check of the result vs the goal
```

### `anthill/web/agents_run.py`'s `run_agent` (verbatim, same shape)

```python
def _backend_for(agent, plane_inf):
    from ..config import Config
    from ..inference.base import build_backend
    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = agent.model or plane_inf.model
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key
    return build_backend(config)

def run_agent(agent, db) -> str:
    ...
    plane_inf = plane_inference(plane, cfg, decrypt=decrypt)  # raises PlaneUnavailable if org down
    backend = _backend_for(agent, plane_inf)
    ...
    result = executor.run(goal, context=mem_ctx)
    return result.answer
```

Caller, verbatim:
```python
            result = run_agent(agent, db) or ""
            agent.last_result = result[:4000]
            _verify_agent_result(agent, result, db)
            _remember_agent_outcome(agent, result, db)
```

### `verify.py`'s existing advisory cross-check - UNCHANGED by this work, per spec R8

R8: "THE SYSTEM SHALL leave verify.py's sequential cross-check unchanged, neither deleted nor
superseded here. THE SYSTEM SHALL treat folding verify into the council path as a future follow-up, out
of scope here." Both `_verify_task_result` and `_verify_agent_result` (in `scheduler.py`) call
`verify.py`'s `verify()`/`crosscheck_for()` AFTER the result string is finalized. This change must run
BEFORE those calls (review, then verify - verify.py ends up checking whatever the council review
produced, exactly the same as it checks whatever a single model produced today - no change to verify.py
itself, no change to its call signature or timing relative to `task.last_result`/`agent.last_result`
being set).

### `AgentResult` (`anthill/agent/executor.py:29-38`, verbatim - what's available to hand to reviewers)

```python
@dataclass
class AgentResult:
    answer: str
    steps: list[StepResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    @property
    def used_tools(self) -> list[str]:
        return [s.tool_name for s in self.steps]
```
`StepResult` (same file) carries `tool_name`, `arguments`, `result`, `duration_ms`, `verify_flag` per
step. Reviewers should see the goal and the final answer at minimum; whether to also summarize
`used_tools`/step results for reviewer context (so they can judge "was calling this tool reasonable,"
not just "does this text read correctly") is an implementation judgement call - state whichever you
choose in the PR description.

### `resolve_council_backends()` (Phase 3, `anthill/council/engine.py`, unchanged, reused here)

Index 0 is always the lead (product convention; kept in sync with the legacy single-model columns by
Phase 1b's `_mirror_lead_into_council()`). Reviewers are `[m for m in resolved if m.index != 0]`. **Do
not re-resolve or rebuild the lead's backend for the revision call** - reuse the exact `backend` object
`_run_task`/`run_agent` already built via `_backend_for`/`build_backend`, so the model that revises the
answer is guaranteed identical to the model that ran the task (no risk of the two resolution paths
drifting apart).

## Design

Add a new function, e.g. `anthill/council/engine.py`'s `review_completed_answer(cfg, goal: str,
draft_answer: str, lead_backend: InferenceBackend, decrypt, *, temperature: float = 0.2) ->
ReviewResult` (exact name/placement/shape is an implementation choice - state it in the PR):

- Resolve council members via `resolve_council_backends(cfg, decrypt)`; take everyone with `index != 0`
  as reviewers. **Reviewers are only ever given `.chat()` - never `.chat_with_tools()`, never a `Tool`
  instance, never the ability to execute anything.** This is the load-bearing safety property of this
  whole change and must be provable by a test (see Acceptance Criteria).
- 0 reviewers (whether because 0-1 total members are configured, or the account only has a lead) ->
  return the draft answer completely unchanged, `reviewed=False`. This must be the overwhelmingly common
  case today (most accounts have no council configured yet) and must add zero latency/cost in that case.
- 1+ reviewers -> send each one, in parallel, the goal and the draft answer, asking for a critique /
  correction. Reuse the EXACT concurrency pattern Phase 3/4a already proved correct (and had to fix
  twice): `concurrent.futures.wait(futures, timeout=...)` (never `as_completed(timeout=...)` +
  `.result(timeout=...)` inside the loop - that combination enforces no timeout), and manage the
  executor manually with `shutdown(wait=False)` in a `finally` (never `with ThreadPoolExecutor(...) as
  ex:` - `__exit__` calls `shutdown(wait=True)`, which silently re-blocks on a hung reviewer). Reuse
  this pattern, don't reinvent it.
- If every reviewer fails/times out, return the draft answer unchanged, `reviewed=True, revised=False`,
  with failures recorded (mirroring `CouncilResult.failures`'s shape).
- Otherwise, one more call on `lead_backend` (NOT re-resolved, the SAME object passed in), given the
  goal, the draft answer, and the surviving reviewers' critiques, asking it to produce a final answer -
  either the original unchanged, or a revision addressing the critiques. This call gets its own timeout,
  wrapped the same safe way (a manually-managed single-worker executor with `shutdown(wait=False)`, not
  a `with` block - see Phase 3's exact fix for why).
- If that revision call fails, return the draft answer unchanged (a review must never make the result
  worse or block the task from completing) - `revised=False`, the failure recorded.
- Nothing here is persisted to the database beyond what already exists (`task.last_result`/
  `agent.last_result` capture only the final answer string, exactly as today) - reviewer critiques are
  not stored, only used in-memory to produce the (possibly revised) final answer. This keeps the change
  free of any schema migration. If richer observability (storing the critiques themselves) turns out to
  be wanted, that's a follow-up, not this change.

### Wiring into `_run_task` / `run_agent`

Modify BOTH functions internally (not their callers) so the external call sites in `scheduler.py`'s tick
loop are completely unchanged - they still receive a plain `str` and still call `_verify_task_result`/
`_verify_agent_result` on it exactly as today:

```python
    result = executor.run(goal, context=ctx)
    answer = result.answer
    # NEW: council review, only when reviewers are configured; degrades to `answer` unchanged otherwise
    review = review_completed_answer(cfg_row, goal, answer, backend, decrypt)
    return review.answer
```

A review failure (any exception anywhere in the review path) must never propagate out of `_run_task`/
`run_agent` - it must degrade to the original `answer`, the same way a single council-review failure
degrades inside `review_completed_answer` itself. Belt-and-suspenders: wrap the call site too, not just
trust the function's internal degradation, exactly like `_verify_task_result`'s existing `except
Exception: pass` (advisory-only, never breaks the task run).

## Acceptance Criteria

1. `review_completed_answer()` (or equivalently named/placed function - state your choice) exists,
   reusing `resolve_council_backends()` and Phase 3/4a's proven timeout/executor-shutdown pattern.
2. 0 or 1 total resolvable council members -> `_run_task`/`run_agent` produce byte-identical results to
   before this change (no review attempted, no added latency).
3. 2+ resolvable members -> reviewers (index != 0) are consulted in parallel after the task/agent run
   completes; the SAME lead backend that ran the task does one more call to produce the final answer.
4. A reviewer is NEVER given tool access - a test must prove this concretely (e.g. a fake reviewer
   backend that would raise/fail loudly if any method other than `.chat()` were ever called on it).
5. Any failure in the review path (reviewer failures, revision-call failure, an unexpected exception)
   degrades to the original single-model answer - a review can only add value or be a no-op, never break
   or worsen a task/agent run.
6. `verify.py`'s existing cross-check (`_verify_task_result`, `_verify_agent_result`) is completely
   unchanged - same function signatures, same call timing relative to `task.last_result`/
   `agent.last_result`, still running on the FINAL string (whatever the review step produced).
7. New tests with fake/injectable backends (no live account, no network) proving all of the above.
8. `ruff check`, `ruff format --check`, `mypy`, and the full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers.

## Explicitly out of scope

- Running the tool-calling loop itself more than once, on more than one model, per task/agent execution
  - this is exactly the safety hazard this change exists to avoid.
- Any change to `verify.py`.
- Persisting reviewer critiques to the database (no schema migration in this change).
- The plain chat surface - that's Phase 4a, already merged.
