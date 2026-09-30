"""Phase 4b: council review for scheduled tasks and agent runs (anthill/council/engine.py's
review_completed_answer()).

Unlike run_council() (parallel independent drafting, safe for chat because chat never calls tools), a
scheduled task or agent run executes its tool-calling loop on ONE model only - review_completed_answer()
never re-runs that loop. Reviewers only ever critique the already-finished answer via .chat() - never
.chat_with_tools(), never a Tool instance - so no reviewer can take a duplicate (or any) action.
"""

import anthill.council.engine as council_eng
from anthill.council.engine import ResolvedMember as _ResolvedMember
from anthill.council.engine import ReviewResult, review_completed_answer


class StrictChatOnlyBackend:
    """A backend whose .chat() returns a plain str (the real InferenceBackend contract) and which
    raises loudly if ANYTHING other than .chat() is ever accessed - the concrete proof that reviewers
    never get tool access."""

    def __init__(self, name, *, reply="ok", fail=False, sleep=0.0):
        self.name = name
        self._reply = reply
        self._fail = fail
        self._sleep = sleep
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self._sleep:
            import time

            time.sleep(self._sleep)
        if self._fail:
            raise RuntimeError(f"{self.name} failed")
        return self._reply  # a plain string - the real InferenceBackend.chat() contract

    def __getattr__(self, name):
        raise RuntimeError(f"reviewer {self.name} must only ever use .chat(), denied: {name}")


def _member(index, backend):
    return _ResolvedMember(index=index, backend=backend, provider="lambda", is_local=False)


def _patch_resolved(monkeypatch, members):
    monkeypatch.setattr(council_eng, "resolve_council_backends", lambda cfg, decrypt: members)


def _noop_decrypt(s):
    return s


# --- 0 or 1 total members: no reviewers, unchanged, no latency --------------------------------------


def test_no_reviewers_returns_the_draft_unchanged(monkeypatch):
    lead = StrictChatOnlyBackend("lead")
    _patch_resolved(monkeypatch, [_member(0, lead)])  # only the lead, no reviewers
    res = review_completed_answer(object(), "goal", "the draft answer", lead, _noop_decrypt)
    assert res == ReviewResult(
        answer="the draft answer", reviewed=False, revised=False, failures=[]
    )
    assert lead.calls == 0  # the lead is never called for review when there's nothing to review


def test_zero_members_resolved_returns_the_draft_unchanged(monkeypatch):
    lead = StrictChatOnlyBackend("lead")
    _patch_resolved(monkeypatch, [])
    res = review_completed_answer(object(), "goal", "the draft answer", lead, _noop_decrypt)
    assert res.reviewed is False
    assert res.answer == "the draft answer"


# --- reviewers never get tool access -----------------------------------------------------------------


def test_reviewers_never_receive_tool_access(monkeypatch):
    lead = StrictChatOnlyBackend("lead", reply="revised answer")
    reviewer1 = StrictChatOnlyBackend("r1", reply="looks fine")
    reviewer2 = StrictChatOnlyBackend("r2", reply="one issue found")
    _patch_resolved(monkeypatch, [_member(0, lead), _member(1, reviewer1), _member(2, reviewer2)])
    # If review_completed_answer ever called anything but .chat() on any backend, __getattr__ above
    # would raise and this call would fail - the absence of an exception IS the proof.
    res = review_completed_answer(object(), "goal", "draft", lead, _noop_decrypt)
    assert res.reviewed is True
    assert res.revised is True
    assert res.answer == "revised answer"
    assert reviewer1.calls == 1
    assert reviewer2.calls == 1
    assert lead.calls == 1  # exactly one revision call, not one per reviewer


# --- the lead's revision call returns a plain string, not an object with .content -------------------


def test_revision_result_is_used_directly_as_a_plain_string(monkeypatch):
    # Regression test for a real bug in the council's draft: it treated fut.result() as an object with
    # a `.content` attribute, but InferenceBackend.chat() returns a plain str everywhere in this
    # codebase. A backend whose chat() returns a bare string (exactly the real contract) must work.
    lead = StrictChatOnlyBackend("lead", reply="a plain string answer")
    reviewer = StrictChatOnlyBackend("r1", reply="a critique")
    _patch_resolved(monkeypatch, [_member(0, lead), _member(1, reviewer)])
    res = review_completed_answer(object(), "goal", "draft", lead, _noop_decrypt)
    assert res.answer == "a plain string answer"


# --- partial reviewer failure: still reviews, still revises -------------------------------------------


def test_partial_reviewer_failure_still_produces_a_revision(monkeypatch):
    lead = StrictChatOnlyBackend("lead", reply="revised")
    ok_reviewer = StrictChatOnlyBackend("r1", reply="a real critique")
    failing_reviewer = StrictChatOnlyBackend("r2", fail=True)
    _patch_resolved(
        monkeypatch, [_member(0, lead), _member(1, ok_reviewer), _member(2, failing_reviewer)]
    )
    res = review_completed_answer(object(), "goal", "draft", lead, _noop_decrypt)
    assert res.reviewed is True
    assert res.revised is True
    assert res.answer == "revised"
    assert any(i == 2 for i, _ in res.failures)


# --- every reviewer fails: degrades to the draft, unchanged -------------------------------------------


def test_all_reviewers_failing_degrades_to_the_draft_unchanged(monkeypatch):
    lead = StrictChatOnlyBackend("lead", reply="should never be used")
    r1 = StrictChatOnlyBackend("r1", fail=True)
    r2 = StrictChatOnlyBackend("r2", fail=True)
    _patch_resolved(monkeypatch, [_member(0, lead), _member(1, r1), _member(2, r2)])
    res = review_completed_answer(object(), "goal", "the original draft", lead, _noop_decrypt)
    assert res.reviewed is True
    assert res.revised is False
    assert res.answer == "the original draft"
    assert lead.calls == 0  # no revision call was ever attempted - nothing survived to revise with
    assert len(res.failures) == 2


# --- the revision call itself failing: degrades to the draft, never raises --------------------------


def test_revision_call_failure_degrades_to_the_draft_unchanged(monkeypatch):
    lead = StrictChatOnlyBackend("lead", fail=True)  # fails when asked to revise
    reviewer = StrictChatOnlyBackend("r1", reply="a critique")
    _patch_resolved(monkeypatch, [_member(0, lead), _member(1, reviewer)])
    res = review_completed_answer(object(), "goal", "the original draft", lead, _noop_decrypt)
    assert res.reviewed is True
    assert res.revised is False
    assert res.answer == "the original draft"
    assert any("revision" in msg for _i, msg in res.failures)


def test_empty_revision_response_degrades_to_the_draft_unchanged(monkeypatch):
    lead = StrictChatOnlyBackend("lead", reply="")  # a valid-but-empty response, not an exception
    reviewer = StrictChatOnlyBackend("r1", reply="a critique")
    _patch_resolved(monkeypatch, [_member(0, lead), _member(1, reviewer)])
    res = review_completed_answer(object(), "goal", "the original draft", lead, _noop_decrypt)
    assert res.revised is False
    assert res.answer == "the original draft"


# --- timeout enforcement reuses Phase 3's proven concurrent.futures.wait pattern ---------------------


def test_a_hung_reviewer_does_not_block_the_review(monkeypatch):
    monkeypatch.setattr(council_eng, "_REVIEW_TIMEOUT_S", 0.2)
    lead = StrictChatOnlyBackend("lead", reply="revised")
    fast_reviewer = StrictChatOnlyBackend("r1", reply="quick critique")
    hung_reviewer = StrictChatOnlyBackend("r2", sleep=5.0)
    _patch_resolved(
        monkeypatch, [_member(0, lead), _member(1, fast_reviewer), _member(2, hung_reviewer)]
    )

    import time

    start = time.monotonic()
    res = review_completed_answer(object(), "goal", "draft", lead, _noop_decrypt)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"the hung reviewer blocked the review: {elapsed:.2f}s"
    assert any("timed out" in msg for i, msg in res.failures if i == 2)
    assert res.revised is True  # the fast reviewer's critique still made it through


# --- the constant collision this change must not reintroduce -----------------------------------------


def test_no_name_collision_with_run_councils_constants():
    # Phase 4b's timeouts must NOT reuse/redefine run_council()'s _MEMBER_TIMEOUT_S/_SYNTHESIS_TIMEOUT_S
    # - a real bug in the council's own draft would have silently shortened run_council()'s propose-layer
    # timeout from 130s to 30s by redefining the same module-level name. _MEMBER_TIMEOUT_S itself moved to
    # 310.0 in PR #661 Tier 0 (kept above Config.timeout's new 300s cold-start socket timeout) - this test
    # pins the current value only to catch an accidental redefinition, not to freeze the number forever.
    assert council_eng._MEMBER_TIMEOUT_S == 310.0
    assert council_eng._SYNTHESIS_TIMEOUT_S == 180.0
    assert council_eng._MAX_PARALLEL == 8
    assert council_eng._REVIEW_TIMEOUT_S != council_eng._MEMBER_TIMEOUT_S
