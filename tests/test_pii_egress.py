"""Privacy invariant: PII is scrubbed BEFORE it leaves the perimeter (the cloud-escalation egress).

`test_scrub_pii.py` covers `scrub()` in isolation. This covers the END-TO-END egress path
(`hybrid/escalate.py::maybe_escalate`): it patches the one seam where org data leaves - `call_cloud` -
captures exactly what would be sent, and asserts no raw PII appears in it, that a clean query is not
over-scrubbed, and that a sufficient local answer never egresses at all. Model-free, so it gates every PR.
"""

import pytest

from anthill.hybrid import escalate as esc
from anthill.hybrid.escalate import HybridPolicy, maybe_escalate
from anthill.hybrid.providers import CloudResult

PII = {
    "EMAIL": "jane.doe@acme-internal.example",
    "CARD": "4111 1111 1111 1111",
    "SSN": "123-45-6789",
    "PHONE": "+1 (415) 555-0142",
    "APIKEY": "sk-live-abc123def456ghi789jklmno",
    "JWT": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.abc123def456",
    "IPV4": "10.20.30.40",
    "IBAN": "DE89 3704 0044 0532 0130 00",
    "MAC": "00:1B:44:11:3A:B7",
    "URL": "https://internal.acme.example/secret-doc",
}


@pytest.fixture
def egress(monkeypatch):
    """Patch the cloud call to capture what would leave; return a helper that runs an escalation."""
    box: dict = {}

    def _rec(provider, messages, *, api_key="", model=None, **kw):
        box["text"] = "\n".join(getattr(m, "content", "") for m in messages)
        box["called"] = True
        return CloudResult(answer="(stub)", provider="stub", model=model or "stub")

    monkeypatch.setattr(esc, "call_cloud", _rec)

    def _run(question, context="", local_answer="", **pol):
        box.clear()
        d = {
            "enabled": True,
            "provider": "openrouter",
            "api_key": "not-a-real-key",
            "threshold": 0.9,
            "send_context": True,
            "scrub_pii": True,
            "monthly_budget_usd": 0.0,
        }
        d.update(pol)
        # Consent is granted here so these tests exercise the egress/scrub path; the fail-closed
        # (no-consent) behaviour is covered in test_escalation_consent.py.
        out = maybe_escalate(
            question,
            local_answer,
            policy=HybridPolicy(**d),
            context=context,
            spent_this_month=0.0,
            consent=lambda proposal: True,
        )
        return out, box.get("text", ""), box.get("called", False)

    return _run


@pytest.mark.parametrize("kind,val", list(PII.items()))
def test_structured_pii_in_question_never_egresses_raw(egress, kind, val):
    out, sent, called = egress(f"Look into this record and advise: {val}. What next?")
    assert out.escalated and called, f"{kind}: did not escalate ({out.reason})"
    assert val not in sent, f"{kind}: RAW PII LEAKED to cloud egress"
    assert out.pii_redacted >= 1, f"{kind}: scrubber did not fire"


def test_pii_in_wiki_context_is_scrubbed_before_egress(egress):
    ctx = f"Customer file - email {PII['EMAIL']}, card {PII['CARD']}, ssn {PII['SSN']}."
    out, sent, _ = egress("Summarise the attached customer file.", context=ctx)
    assert out.escalated
    for kind in ("EMAIL", "CARD", "SSN"):
        assert PII[kind] not in sent, f"context {kind}: RAW PII LEAKED via wiki context"


def test_clean_query_is_not_over_scrubbed(egress):
    out, sent, _ = egress("Summarise our Q3 hiring plan and the three product priorities.")
    assert out.escalated
    assert out.pii_redacted == 0, "clean query should have zero redactions"
    assert "hiring plan" in sent, "clean query content must survive"


def test_sufficient_local_answer_never_calls_the_cloud(egress):
    good = (
        "Our Q3 plan hires two engineers and a designer; priorities are onboarding, billing, and "
        "the mobile app, each with an owner and a target date."
    )
    out, _sent, called = egress("What is our Q3 plan?", local_answer=good, threshold=0.1)
    assert not out.escalated and not called, "a sufficient local answer must not egress at all"


def test_scrub_toggle_off_still_never_leaks_when_default_on():
    # documents the default: scrub_pii defaults True, so the invariant holds out of the box
    assert HybridPolicy().scrub_pii is True
