"""When a weak answer is escalated to a cloud model, PII is scrubbed on the way out AND restored on
the way back, so the redaction is transparent to the user.

Security review: the outbound prompt was scrubbed (email/phone/etc. -> ``[EMAIL_1]`` placeholders) but
the cloud answer was returned verbatim. A cloud model that echoed a placeholder left the user reading
``[EMAIL_1]`` instead of the real value. ``restore()`` now runs on the answer; the placeholder->original
map is built locally and never leaves the machine.

(The paid cloud-escalation path is currently retired from the UI and off by default; this keeps it
correct for when it is revisited.)
"""

from anthill.hybrid import escalate as esc
from anthill.hybrid.escalate import HybridPolicy, maybe_escalate
from anthill.hybrid.providers import CloudResult


def _policy():
    return HybridPolicy(
        enabled=True, provider="openrouter", api_key="k", scrub_pii=True, send_context=True
    )


def test_placeholder_echoed_by_the_cloud_is_restored(monkeypatch):
    seen = {}

    def _fake_call_cloud(provider, messages, *, api_key, model=None):
        seen["outbound"] = messages[-1].content  # what actually left the machine
        # A cloud model that parrots the placeholder back in its reply.
        return CloudResult(
            answer="Sure, I'll email [EMAIL_1] now.", provider="openrouter", model="x"
        )

    monkeypatch.setattr(esc, "call_cloud", _fake_call_cloud)

    out = maybe_escalate(
        "draft a note",
        "I don't know.",  # weak local answer -> escalate
        policy=_policy(),
        context="reach me at alice@example.com",
        consent=lambda proposal: True,  # per-use consent granted (#252)
    )
    assert out.escalated
    # Outbound: the real address was scrubbed before the network call.
    assert "alice@example.com" not in seen["outbound"] and "[EMAIL_1]" in seen["outbound"]
    # Inbound: the user reads the real value, not the placeholder.
    assert "alice@example.com" in out.answer and "[EMAIL_1]" not in out.answer


def test_clean_question_round_trips_unchanged(monkeypatch):
    def _fake_call_cloud(provider, messages, *, api_key, model=None):
        return CloudResult(answer="The capital is Paris.", provider="openrouter", model="x")

    monkeypatch.setattr(esc, "call_cloud", _fake_call_cloud)
    out = maybe_escalate(
        "capital of France?", "I don't know.", policy=_policy(), consent=lambda proposal: True
    )
    assert out.escalated and "The capital is Paris." in out.answer
    assert out.pii_redacted == 0
