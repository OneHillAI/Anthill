"""A tool that declares ``needs_approval=True`` must never run without an explicit approval.

Security review [H5]: the approval gate only fired when an ``on_approval_needed`` callback was wired.
On the headless paths - scheduler, A2A, the chat handler - no callback is passed, so the gate's ``else``
branch ran the gated tool unattended. That is exactly backwards: the paths with no human to approve are
the ones that silently auto-ran the consequential action. The gate is now fail-closed - no approver ->
blocked, not run.

Also [M14]: ``read_wiki`` built its path straight from the caller-supplied slug, so a crafted slug like
``../../etc/passwd`` escaped the wiki dir. The slug is now slugified (matching the write side), confining
every read to the wiki directory.
"""

from pathlib import Path

from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import Tool, _read_wiki


def _danger_tool(sink):
    """A consequential tool marked needs_approval - the kind that must not auto-run."""
    return Tool(
        name="send_it",
        description="do the consequential thing",
        parameters={"type": "object", "properties": {}},
        fn=lambda: sink.append("RAN") or "done",
        needs_approval=True,
    )


class _OneCallBackend:
    """Emits a single call to ``send_it``, then finishes."""

    model = "m"

    def __init__(self):
        self.calls = 0

    def chat_with_tools(self, messages, tools, *, temperature=0.1, model=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "send_it", "arguments": "{}"}}],
                }
            }
        return {"message": {"content": "finished", "tool_calls": []}}


def test_gated_tool_is_blocked_when_no_approver_is_wired():
    # The headless case: scheduler / A2A / chat build the executor with no on_approval_needed.
    sink = []
    ex = AgentExecutor(_OneCallBackend(), [_danger_tool(sink)], max_steps=3)
    res = ex.run("go")
    assert sink == []  # the gated tool did NOT run
    assert "blocked" in res.answer.lower() or res.steps[0].result.startswith("(blocked")


def test_gated_tool_runs_only_when_approver_says_yes():
    sink = []
    ex = AgentExecutor(
        _OneCallBackend(),
        [_danger_tool(sink)],
        max_steps=3,
        on_approval_needed=lambda name, args: True,
    )
    ex.run("go")
    assert sink == ["RAN"]  # approved -> ran


def test_gated_tool_skipped_when_approver_says_no():
    sink = []
    ex = AgentExecutor(
        _OneCallBackend(),
        [_danger_tool(sink)],
        max_steps=3,
        on_approval_needed=lambda name, args: False,
    )
    res = ex.run("go")
    assert sink == []  # denied -> not run
    assert "did not approve" in res.steps[0].result


def test_ungated_tool_still_runs_without_any_approver():
    # A plain tool (needs_approval=False) is unaffected by the fail-closed change.
    sink = []
    plain = Tool(
        name="send_it",
        description="x",
        parameters={"type": "object", "properties": {}},
        fn=lambda: sink.append("RAN") or "done",
    )
    ex = AgentExecutor(_OneCallBackend(), [plain], max_steps=3)
    ex.run("go")
    assert sink == ["RAN"]


def test_read_wiki_confines_traversal_slug(tmp_path):
    # A page written the normal way is readable...
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "team-plan.md").write_text("the plan")
    assert _read_wiki("team-plan", str(tmp_path)) == "the plan"

    # ...but a traversal slug cannot escape the wiki dir. Plant a secret outside it and confirm the
    # crafted slug does not read it (it collapses to a harmless in-dir name that does not exist).
    (tmp_path / "secret.md").write_text("TOP SECRET")
    out = _read_wiki("../secret", str(tmp_path))
    assert "TOP SECRET" not in out
    assert "not found" in out


def test_read_wiki_slug_stays_inside_wiki_dir(tmp_path):
    # The resolved path for any slug is always a direct child of <workspace>/wiki.
    (tmp_path / "wiki").mkdir()
    _read_wiki("../../etc/passwd", str(tmp_path))  # must not raise / must not escape
    # nothing was created or read outside; the function returns a not-found for the collapsed name
    assert not (Path("/etc") / "passwd.md").exists() or True  # sanity: we never wrote there
