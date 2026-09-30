"""Expert-tier escalation trigger logic (compound-compute-tiers spec): Ask mode's free hedge-OR-
confidence signal (intent.seems_uncertain_for_ask) and Automated mode's logprob-first-then-one-local-
grader-call decision (escalation.should_escalate_automated / grade_answer_locally). Both share the
CONFIDENCE_UNCERTAIN/CONFIDENCE_CONFIDENT thresholds from anthill.agent.intent.
"""

from anthill.agent.intent import (
    CONFIDENCE_CONFIDENT,
    CONFIDENCE_UNCERTAIN,
    seems_uncertain_for_ask,
)
from anthill.inference.base import ChatResult
from anthill.web.escalation import grade_answer_locally, should_escalate_automated

# ── seems_uncertain_for_ask (Ask mode, free - no extra call) ────────────────────────────


def test_ask_true_on_text_hedge_regardless_of_confidence():
    assert seems_uncertain_for_ask("I'm not sure, but it's probably X.", confidence=-0.1) is True


def test_ask_true_on_low_confidence_alone():
    assert seems_uncertain_for_ask("It is X.", confidence=CONFIDENCE_UNCERTAIN - 0.1) is True


def test_ask_false_on_confident_answer_no_hedge():
    assert seems_uncertain_for_ask("It is X.", confidence=CONFIDENCE_CONFIDENT) is False


def test_ask_false_when_confidence_none_and_no_hedge():
    assert seems_uncertain_for_ask("It is X.", confidence=None) is False


def test_ask_confidence_exactly_at_uncertain_threshold_is_not_below_it():
    # < is strict, so the boundary value itself does not trigger the confidence leg alone.
    assert seems_uncertain_for_ask("It is X.", confidence=CONFIDENCE_UNCERTAIN) is False


# ── should_escalate_automated / grade_answer_locally (Automated mode) ───────────────────


class _FakeBackend:
    def __init__(self, reply: str | None = None, raise_error: bool = False):
        self._reply = reply
        self._raise_error = raise_error
        self.called = False

    def chat(self, messages, *, temperature=0.2):
        self.called = True
        if self._raise_error:
            raise RuntimeError("backend unreachable")
        return self._reply


def test_automated_skips_grader_when_confidently_confident():
    backend = _FakeBackend(reply="CONFIDENT")
    result = ChatResult(text="It is X.", confidence=CONFIDENCE_CONFIDENT)
    assert should_escalate_automated(backend, "what is X?", result) is False
    assert backend.called is False


def test_automated_escalates_directly_when_confidently_uncertain_no_grader_call():
    backend = _FakeBackend(reply="CONFIDENT")  # would say don't escalate, but must not be consulted
    result = ChatResult(text="It is X.", confidence=CONFIDENCE_UNCERTAIN - 0.1)
    assert should_escalate_automated(backend, "what is X?", result) is True
    assert backend.called is False


def test_automated_calls_grader_when_confidence_is_borderline():
    midpoint = (CONFIDENCE_CONFIDENT + CONFIDENCE_UNCERTAIN) / 2
    backend = _FakeBackend(reply="CONFIDENT")
    result = ChatResult(text="It is X.", confidence=midpoint)
    assert should_escalate_automated(backend, "what is X?", result) is False
    assert backend.called is True


def test_automated_calls_grader_when_confidence_is_none():
    backend = _FakeBackend(reply="UNSURE")
    result = ChatResult(text="It is X.", confidence=None)
    assert should_escalate_automated(backend, "what is X?", result) is True
    assert backend.called is True


def test_grade_answer_locally_escalates_on_ambiguous_reply():
    backend = _FakeBackend(reply="I'm not sure how to rate that.")
    assert grade_answer_locally(backend, "q", "a") is True


def test_grade_answer_locally_escalates_on_backend_error():
    backend = _FakeBackend(raise_error=True)
    assert grade_answer_locally(backend, "q", "a") is True


def test_grade_answer_locally_no_escalate_on_clean_confident_reply():
    backend = _FakeBackend(reply="CONFIDENT")
    assert grade_answer_locally(backend, "q", "a") is False
