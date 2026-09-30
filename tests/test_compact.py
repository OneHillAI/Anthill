"""In-session context compaction: summarize older turns, keep recent verbatim."""

from anthill.agent.compact import compact_messages, estimate_tokens, messages_tokens
from anthill.inference.base import Message


class _Backend:
    def __init__(self, reply="SUMMARY: decisions + findings."):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply


def _convo(n):
    msgs = [Message("system", "You are an agent.")]
    for i in range(n):
        msgs.append(Message("assistant", f"step {i}: " + "x" * 200))
        msgs.append(Message("tool", f"result {i}: " + "y" * 200))
    return msgs


def test_under_budget_is_unchanged():
    b = _Backend()
    msgs = _convo(2)
    out = compact_messages(msgs, b, max_tokens=100000, keep_recent=6)
    assert out is msgs and b.calls == 0  # no summary call when it fits


def test_over_budget_compacts():
    b = _Backend()
    msgs = _convo(20)  # well over budget
    before = messages_tokens(msgs)
    out = compact_messages(msgs, b, max_tokens=500, keep_recent=4)
    assert b.calls == 1  # summarized once
    assert messages_tokens(out) < before  # smaller
    assert out[0].role == "system" and out[0].content == "You are an agent."  # system kept
    assert "[Summary of earlier steps" in out[1].content  # summary inserted
    assert out[-4:] == msgs[-4:]  # last keep_recent kept verbatim


def test_keep_recent_respected():
    out = compact_messages(_convo(20), _Backend(), max_tokens=500, keep_recent=6)
    # system + summary + 6 recent = 8 messages
    assert len(out) == 8


def test_summary_failure_falls_back_to_truncation():
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("model down")

    out = compact_messages(_convo(20), _Boom(), max_tokens=500, keep_recent=4)
    # still compacted (truncated), never raises
    assert out[0].role == "system"
    assert "[Summary of earlier steps" in out[1].content


def test_estimate_tokens_monotonic():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("x" * 400) > estimate_tokens("x" * 40)
