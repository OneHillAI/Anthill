"""Runtime verifier hooked into the agent executor for consequential actions (Phase D). After a tool
marked needs_approval runs, an advisory cross-check judges whether the action was consistent with the
goal; a flagged action is surfaced (StepResult.verify_flag / a stream warning) but NEVER blocked - the
action already ran. Non-consequential tools are not verified. Spec: RUNTIME_CROSSCHECK_VERIFIER.md."""

from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import Tool
from anthill.verify import Verdict


def _tool(sink, *, needs_approval):
    return Tool(
        name="act",
        description="do a thing",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        fn=lambda x="": sink.append(x) or f"did {x}",
        needs_approval=needs_approval,
    )


class _OneCallBackend:
    """Emits a single call to tool `act`, then a final answer (normalized Ollama shape)."""

    model = "m"

    def __init__(self):
        self.calls = 0

    def chat_with_tools(self, messages, tools, *, temperature=0.1, model=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "act", "arguments": '{"x": "it"}'}}],
                }
            }
        return {"message": {"content": "done", "tool_calls": []}}


def _flag(reason):
    return lambda tn, args, res, *, goal, context: Verdict(
        ok=False, confidence=0.6, reason=reason, kind="action"
    )


def _clean():
    return lambda tn, args, res, *, goal, context: Verdict(
        ok=True, confidence=0.85, reason="consistent", kind="action", needs_review=False
    )


def _approve(tool_name, arguments):
    # The verifier only judges a consequential (needs_approval) action that actually RAN. The gate is
    # fail-closed, so running one now requires an approver to say yes first - these tests wire a yes.
    return True


def test_consequential_action_flagged_records_the_reason():
    ex = AgentExecutor(
        _OneCallBackend(),
        [_tool([], needs_approval=True)],
        max_steps=3,
        on_approval_needed=_approve,
        on_action_verify=_flag("sends to an address not in the goal"),
    )
    res = ex.run("email the team about the launch")
    assert len(res.steps) == 1
    assert res.steps[0].verify_flag == "sends to an address not in the goal"


def test_clean_action_leaves_the_flag_empty():
    ex = AgentExecutor(
        _OneCallBackend(),
        [_tool([], needs_approval=True)],
        max_steps=3,
        on_approval_needed=_approve,
        on_action_verify=_clean(),
    )
    res = ex.run("do it")
    assert res.steps[0].verify_flag == ""


def test_non_consequential_tool_is_not_verified():
    calls = []

    def _spy(tn, args, res, *, goal, context):
        calls.append(tn)
        return Verdict(ok=False, confidence=0.6, reason="x", kind="action")

    sink = []
    ex = AgentExecutor(
        _OneCallBackend(), [_tool(sink, needs_approval=False)], max_steps=3, on_action_verify=_spy
    )
    res = ex.run("do it")
    assert sink == ["it"]  # the tool ran
    assert calls == []  # ...but a non-consequential tool is never sent to the verifier
    assert res.steps[0].verify_flag == ""


def test_verify_hook_is_best_effort_and_never_breaks_the_run():
    def _boom(tn, args, res, *, goal, context):
        raise RuntimeError("verifier exploded")

    sink = []
    ex = AgentExecutor(
        _OneCallBackend(),
        [_tool(sink, needs_approval=True)],
        max_steps=3,
        on_approval_needed=_approve,
        on_action_verify=_boom,
    )
    res = ex.run("do it")
    assert res.answer == "done" and sink == ["it"]  # the run completed and the action ran
    assert res.steps[0].verify_flag == ""


def test_stream_yields_a_verifier_warning_for_a_flagged_action():
    ex = AgentExecutor(
        _OneCallBackend(),
        [_tool([], needs_approval=True)],
        max_steps=3,
        on_approval_needed=_approve,
        on_action_verify=_flag("inconsistent with the goal"),
    )
    out = "".join(ex.stream("do it"))
    assert "verifier: inconsistent with the goal" in out


def test_no_hook_means_no_verification_and_no_change():
    # without on_action_verify (but with the action approved) the flag stays empty and the action runs
    sink = []
    ex = AgentExecutor(
        _OneCallBackend(),
        [_tool(sink, needs_approval=True)],
        max_steps=3,
        on_approval_needed=_approve,
    )
    res = ex.run("do it")
    assert sink == ["it"] and res.steps[0].verify_flag == ""
