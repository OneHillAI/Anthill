# Tasks

- [ ] Add `review_completed_answer(cfg, goal: str, draft_answer: str, lead_backend: InferenceBackend,
  decrypt, *, temperature: float = 0.2) -> ReviewResult` (exact name/module/shape is an implementation
  choice) to the council package, reusing `resolve_council_backends()` and Phase 3/4a's proven
  `concurrent.futures.wait(timeout=...)` + manual `shutdown(wait=False)` pattern - do not reintroduce
  the `as_completed(timeout=...)` bug or the `with ThreadPoolExecutor(...) as ex:` shutdown-blocking bug
  earlier phases already found and fixed. See proposal.md's Design section for the exact behavior at 0
  reviewers / all-reviewers-fail / revision-call-fails.
- [ ] Wire it into `anthill/web/scheduler.py`'s `_run_task` - after `result = executor.run(goal,
  context=ctx)`, before `return result.answer`. The function's external return type/behavior for its
  caller is unchanged (still returns a plain `str`).
- [ ] Wire it into `anthill/web/agents_run.py`'s `run_agent` - same pattern, after `result =
  executor.run(goal, context=mem_ctx)`, before `return result.answer`.
- [ ] Wrap both call sites so ANY exception in the review path degrades to the original `result.answer`
  - a review must never break or block a task/agent run.
- [ ] New tests (fake/injectable backends, no live account/network) proving the acceptance criteria in
  proposal.md, in particular: a fake reviewer backend that raises/fails if any method OTHER than
  `.chat()` is ever called on it (proves reviewers never get tool access).

## Reference: `anthill/web/scheduler.py`'s `_run_task` - exact current code, the integration point

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
    executor = AgentExecutor(backend, tools, ...)
    result = executor.run(goal, context=ctx)
    return result.answer          # <-- INTEGRATION POINT: review result.answer before returning it
```
`cfg_row` and `decrypt` (the module-level import, `.crypto.decrypt`) are both already in scope right
here - `review_completed_answer(cfg_row, goal, result.answer, backend, decrypt)` needs nothing new
threaded in from the caller.

## Reference: `anthill/web/agents_run.py` - exact current code, the integration point

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
    plane_inf = plane_inference(plane, cfg, decrypt=decrypt)
    backend = _backend_for(agent, plane_inf)
    ...
    executor = AgentExecutor(backend, tools, ...)
    result = executor.run(goal, context=mem_ctx)
    return result.answer          # <-- INTEGRATION POINT, same shape as _run_task above
```
`cfg` and `decrypt` are already in scope in this function too (used a few lines above for
`plane_inference(...)`).

## Reference: what must stay untouched - `scheduler.py`'s tick-loop callers (verbatim)

```python
            result = _run_task(task, db) or ""
            task.status = "done"
            task.last_result = result[:4000]
            _verify_task_result(task, result, db)
```
```python
            result = run_agent(agent, db) or ""
            agent.last_result = result[:4000]
            _verify_agent_result(agent, result, db)
            _remember_agent_outcome(agent, result, db)
```
Neither of these call sites changes. `_verify_task_result`/`_verify_agent_result` (verify.py's advisory
cross-check) run exactly as today, on whatever `_run_task`/`run_agent` return - which, after this
change, may be a council-reviewed/revised answer instead of the raw single-model one. That is the
intended interaction: review, then verify - not a change to verify.py.

## Reference: Phase 3/4a's engine.py concurrency pattern to reuse (verbatim - the exact fix, don't
reinvent it)

```python
    max_workers = min(len(resolved), _MAX_PARALLEL)
    # Deliberately NOT a "with ThreadPoolExecutor(...) as ex:" block: ThreadPoolExecutor.__exit__
    # calls shutdown(wait=True), which blocks until every submitted thread finishes - including any
    # that concurrent.futures.wait() below already gave up on.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_member = {ex.submit(m.backend.chat, messages, temperature=temperature): m for m in resolved}
        done, not_done = concurrent.futures.wait(future_to_member, timeout=_MEMBER_TIMEOUT_S)
        # collect from done, mark not_done as timed-out, fut.cancel() on each (best-effort)
    finally:
        ex.shutdown(wait=False)
```
Same shape for the single lead revision call: a manually-managed single-worker executor, `finally:
ex.shutdown(wait=False)`, never a bare `with` block.

## Reference: `AgentResult` / `StepResult` (`anthill/agent/executor.py`, verbatim)

```python
@dataclass
class StepResult:
    step: int
    tool_name: str
    arguments: dict
    result: str
    duration_ms: int
    verify_flag: str = ""

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

## Reference: `run_council()`'s current (Phase 4a) messages-based signature (verbatim,
`anthill/council/engine.py`) - for context on the pattern `review_completed_answer` should mirror, NOT
something this task calls directly (chat-style propose+synthesize is a different shape from
review-a-finished-answer)

```python
def run_council(
    cfg,
    messages: Sequence[Message],
    decrypt: Callable[[str], str],
    *,
    temperature: float = 0.2,
) -> CouncilResult:
```
`review_completed_answer` is a NEW, DIFFERENT function - it does not call `run_council()` internally,
since the shape is different (one draft to critique, not N independent proposals to synthesize).
