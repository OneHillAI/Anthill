"""Phase 3: the Mixture-of-Agents (MoA) orchestration engine (anthill/council/engine.py).

Standalone engine only - NOT wired into ask.py/scheduler.py/agents_run.py (Phase 4). Drafted by the
ASDD dev-council, then reviewed by hand: the council's own draft had a real bug in the propose layer's
timeout - concurrent.futures.as_completed() only ever yields an ALREADY-FINISHED future, so wrapping its
result in fut.result(timeout=...) enforces nothing (proven below and in test_engine_timeout_bug.py-style
reasoning inline). Fixed with concurrent.futures.wait(timeout=...), which genuinely bounds the wait.
test_propose_layer_actually_enforces_its_timeout below is the regression test that would fail against
the original draft's code and passes against the fix.
"""

import threading
import time
import types

import pytest

from anthill.council.engine import (
    AllMembersFailed,
    NoMembersResolved,
    ResolvedMember,
    resolve_council_backends,
    run_council,
)
from anthill.inference.base import Message


def _q(text, system=None):
    msgs = []
    if system:
        msgs.append(Message(role="system", content=system))
    msgs.append(Message(role="user", content=text))
    return msgs


# --- Fake backend implementing the InferenceBackend Protocol ----------------------------------------


class FakeBackend:
    def __init__(self, model, *, reply="ok", sleep=0.0, fail=False):
        self.model = model
        self._reply = reply
        self._sleep = sleep
        self._fail = fail
        self.calls = []

    def chat(self, messages, *, temperature=0.2):
        self.calls.append(list(messages))
        if self._sleep:
            time.sleep(self._sleep)
        if self._fail:
            raise RuntimeError(f"{self.model} boom")
        return self._reply

    def health(self):
        return None


class _MeetingBackend(FakeBackend):
    """On its first call, waits for the other members at a barrier. It only gets through if all of them are
    inside chat() at the same moment, so it proves concurrency without measuring how fast the machine is."""

    def __init__(self, model, barrier, **kw):
        super().__init__(model, **kw)
        self._barrier = barrier
        self.met = False

    def chat(self, messages, *, temperature=0.2):
        if not self.calls and self._barrier is not None:
            self._barrier.wait()  # raises BrokenBarrierError after its timeout if the others never arrive
            self.met = True
        return super().chat(messages, temperature=temperature)


def _cfg_with_members(members_json):
    return types.SimpleNamespace(org_council_members=members_json)


def _noop_decrypt(s):
    return s  # tests store plaintext in model_key_enc


def _member(idx, **over):
    m = {
        "endpoint": f"http://m{idx}",
        "provider": "lambda",
        "model": f"model-{idx}",
        "model_key_enc": "",
        "lifecycle": "vpc",
    }
    m.update(over)
    return m


# --- member resolution -------------------------------------------------------------------------------


def test_resolver_skips_unrecognized_lifecycle_and_blanks(monkeypatch):
    import json

    import anthill.council.engine as eng

    built = []

    def fake_build_backend(config):
        built.append(config.model)
        return FakeBackend(config.model)

    monkeypatch.setattr(eng, "build_backend", fake_build_backend)

    cfg = _cfg_with_members(
        json.dumps(
            [
                _member(0),  # ok
                _member(1, lifecycle="inference-provider"),  # Phase 5 -> skip
                _member(2, endpoint=""),  # blank endpoint -> skip
                _member(3, model=""),  # blank model -> skip
                _member(4, lifecycle="bogus"),  # unknown lifecycle -> skip
                _member(5),  # ok
            ]
        )
    )

    resolved = resolve_council_backends(cfg, _noop_decrypt)
    assert [r.index for r in resolved] == [0, 5]
    assert built == ["model-0", "model-5"]


def test_resolver_maps_onprem_to_ollama_and_others_to_openai(monkeypatch):
    import json

    import anthill.council.engine as eng

    seen = {}

    def fake_build_backend(config):
        seen[config.model] = config.backend
        return FakeBackend(config.model)

    monkeypatch.setattr(eng, "build_backend", fake_build_backend)
    cfg = _cfg_with_members(
        json.dumps([_member(0, provider="onprem"), _member(1, provider="lambda")])
    )
    resolve_council_backends(cfg, _noop_decrypt)
    assert seen["model-0"] == "ollama"
    assert seen["model-1"] == "openai"


def test_resolver_skips_a_member_whose_key_fails_to_decrypt(monkeypatch):
    import json

    import anthill.council.engine as eng

    monkeypatch.setattr(eng, "build_backend", lambda config: FakeBackend(config.model))

    def boom(_s):
        raise ValueError("bad ciphertext")

    cfg = _cfg_with_members(json.dumps([_member(0, model_key_enc="not-really-encrypted")]))
    resolved = resolve_council_backends(cfg, boom)
    assert resolved == []


def test_resolver_empty_or_unparseable_config_returns_nothing():
    assert (
        resolve_council_backends(types.SimpleNamespace(org_council_members=""), _noop_decrypt) == []
    )
    assert (
        resolve_council_backends(
            types.SimpleNamespace(org_council_members="not json"), _noop_decrypt
        )
        == []
    )


# --- helper: patch resolve_council_backends to hand back fixed fakes --------------------------------


def _patch_resolved(monkeypatch, fakes):
    import anthill.council.engine as eng

    resolved = [
        ResolvedMember(index=i, backend=f, provider="lambda", is_local=False)
        for i, f in enumerate(fakes)
    ]
    monkeypatch.setattr(eng, "resolve_council_backends", lambda cfg, decrypt: resolved)


# --- 0 members ------------------------------------------------------------------------------------


def test_no_members_resolved_raises(monkeypatch):
    _patch_resolved(monkeypatch, [])
    with pytest.raises(NoMembersResolved):
        run_council(object(), _q("q?"), _noop_decrypt)


# --- 1 member = direct path, no synthesis ------------------------------------------------------------


def test_single_member_direct_no_synthesis(monkeypatch):
    lead = FakeBackend("lead", reply="direct answer")
    _patch_resolved(monkeypatch, [lead])
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.answer == "direct answer"
    assert res.synthesized is False
    assert len(lead.calls) == 1  # exactly one chat call, no synthesis round-trip


def test_synthesis_derives_question_and_system_from_the_messages_list(monkeypatch):
    # run_council() takes a full messages list (Phase 4a), not a bare (question, system) pair -
    # _synthesis_messages must derive both from it: the system prompt is preserved into the
    # synthesizer's own system message, and "the original question" shown to the synthesizer is the
    # last user message, not the whole conversation.
    fakes = [
        FakeBackend("m0", reply="draft0"),
        FakeBackend("m1", reply="draft1"),
    ]
    _patch_resolved(monkeypatch, fakes)
    messages = _q("What's the capital of France?", system="You are a geography tutor.")
    res = run_council(object(), messages, _noop_decrypt)
    assert res.synthesized is True
    synth_messages = fakes[0].calls[-1]  # m0 is the lead; its 2nd call is the synthesis call
    synth_system = next(m.content for m in synth_messages if m.role == "system")
    synth_user = next(m.content for m in synth_messages if m.role == "user")
    assert "You are a geography tutor." in synth_system
    assert "What's the capital of France?" in synth_user


def test_synthesized_result_carries_each_members_own_draft(monkeypatch):
    # Previously CouncilResult discarded every member's own draft the moment synthesis ran - only the
    # flattened final answer survived. `proposals` banks them so the full reasoning process, not just
    # the result, can be captured as training data later.
    fakes = [
        FakeBackend("m0", reply="draft0"),
        FakeBackend("m1", reply="draft1"),
    ]
    _patch_resolved(monkeypatch, fakes)
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.synthesized is True
    assert res.proposals == [(0, "draft0"), (1, "draft1")]


def test_single_member_result_has_no_proposals_to_bank(monkeypatch):
    # synthesized=False paths (single member, or a lone surviving proposer) have nothing beyond the
    # one already-returned answer - proposals stays empty rather than duplicating it.
    lead = FakeBackend("lead", reply="direct answer")
    _patch_resolved(monkeypatch, [lead])
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.synthesized is False
    assert res.proposals == []


# --- true parallel execution --------------------------------------------------------------------------


def test_propose_layer_runs_in_parallel(monkeypatch):
    # The three proposers must be inside chat() at the same moment: each waits for the others at a barrier on
    # its first call, so the test passes only if the engine really runs them concurrently, whatever the speed of
    # the machine. (An earlier version timed the whole run against a 1.1s limit and failed on a busy CI runner.)
    barrier = threading.Barrier(3, timeout=3)
    fakes = [_MeetingBackend(f"m{i}", barrier, reply=f"draft{i}") for i in range(3)]
    _patch_resolved(monkeypatch, fakes)

    res = run_council(object(), _q("q?"), _noop_decrypt)

    assert res.synthesized is True
    assert all(f.met for f in fakes), "the propose layer did not run the members at the same time"


def test_propose_layer_actually_enforces_its_timeout(monkeypatch):
    # Regression test for the bug found in the council's draft: as_completed() only ever yields an
    # ALREADY-DONE future, so a fut.result(timeout=...) call inside that loop enforces nothing - a
    # hung member would block the whole propose layer indefinitely despite a "timeout" being set. This
    # shrinks the engine's timeout way below a deliberately-hung member's sleep and asserts the call
    # returns promptly anyway (proving the timeout is real, not decorative).
    import anthill.council.engine as eng

    monkeypatch.setattr(eng, "_MEMBER_TIMEOUT_S", 0.2)
    hung = FakeBackend("hung", sleep=5.0)  # far longer than the shrunk timeout
    fast = FakeBackend("fast", reply="fast answer")
    _patch_resolved(monkeypatch, [fast, hung])

    start = time.monotonic()
    res = run_council(object(), _q("q?"), _noop_decrypt)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"the propose layer waited for the hung member: {elapsed:.2f}s"
    assert 1 not in res.proposer_indexes  # the hung member never contributed a proposal
    assert any("timed out" in msg for i, msg in res.failures if i == 1)


def test_propose_layer_does_not_block_shutdown_on_a_hung_thread(monkeypatch):
    # Regression test for a second bug found while writing the test above: even after switching to
    # concurrent.futures.wait(timeout=...), wrapping the executor in "with ThreadPoolExecutor(...) as
    # ex:" still blocked the whole function on exit, because ThreadPoolExecutor.__exit__ always calls
    # shutdown(wait=True) - which waits for the hung member's thread to actually finish (its full sleep)
    # regardless of what the timeout logic decided. A 3-member case (2 hung, 1 fast) makes this
    # unambiguous: without the fix this takes >2s (bounded by the hung members' sleep), with the fix
    # it returns promptly.
    import anthill.council.engine as eng

    monkeypatch.setattr(eng, "_MEMBER_TIMEOUT_S", 0.2)
    fakes = [
        FakeBackend("fast", reply="fast answer"),
        FakeBackend("h1", sleep=2.0),
        FakeBackend("h2", sleep=2.0),
    ]
    _patch_resolved(monkeypatch, fakes)

    start = time.monotonic()
    res = run_council(object(), _q("q?"), _noop_decrypt)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"shutdown blocked on a hung thread: {elapsed:.2f}s"
    assert res.proposer_indexes == [0]


# --- partial failure: one fails, others succeed -> still answers -------------------------------------


def test_partial_failure_still_answers(monkeypatch):
    fakes = [
        FakeBackend("m0", reply="draft0"),
        FakeBackend("m1", fail=True),
        FakeBackend("m2", reply="draft2"),
    ]
    _patch_resolved(monkeypatch, fakes)
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert 1 not in res.proposer_indexes
    assert set(res.proposer_indexes) == {0, 2}
    assert any(i == 1 for i, _ in res.failures)
    assert res.synthesized is True
    assert res.answer == fakes[0]._reply  # the synthesizer (lead, m0) returned its own reply


def test_only_one_proposer_survives_no_synthesis(monkeypatch):
    fakes = [FakeBackend("m0", fail=True), FakeBackend("m1", reply="lone draft")]
    _patch_resolved(monkeypatch, fakes)
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.answer == "lone draft"
    assert res.synthesized is False
    assert res.proposer_indexes == [1]


def test_lead_failing_falls_back_to_next_surviving_proposer_as_synthesiser(monkeypatch):
    # The lead (index 0) fails to PROPOSE; synthesis must fall back to the lowest-index survivor
    # (index 1) rather than crashing or silently dropping the lead's role.
    fakes = [
        FakeBackend("m0", fail=True),
        FakeBackend("m1", reply="m1's synthesis"),
        FakeBackend("m2", reply="m2's draft"),
    ]
    _patch_resolved(monkeypatch, fakes)
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.synthesized is True
    assert res.answer == "m1's synthesis"  # m1 (lowest surviving index) did the synthesis call
    assert len(fakes[1].calls) == 2  # once as a proposer, once as the synthesiser


# --- all members fail -> distinguishable failure ------------------------------------------------------


def test_all_fail_raises_all_members_failed(monkeypatch):
    fakes = [FakeBackend(f"m{i}", fail=True) for i in range(3)]
    _patch_resolved(monkeypatch, fakes)
    with pytest.raises(AllMembersFailed) as exc_info:
        run_council(object(), _q("q?"), _noop_decrypt)
    assert len(exc_info.value.errors) == 3


# --- synthesis failure degrades gracefully -------------------------------------------------------------


def test_synthesis_failure_degrades_to_top_proposal(monkeypatch):
    fakes = [
        FakeBackend("m0", fail=True),  # m0 proposes fine, then fails when asked to synthesise
        FakeBackend("m1", reply="m1 draft"),
    ]
    # m0 succeeds at proposing but fails at synthesis: swap behavior after the first call.
    calls = {"n": 0}

    def flaky_chat(messages, *, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            return "m0 draft"
        raise RuntimeError("m0 unavailable for synthesis")

    fakes[0].chat = flaky_chat
    _patch_resolved(monkeypatch, fakes)
    res = run_council(object(), _q("q?"), _noop_decrypt)
    assert res.synthesized is False
    assert res.answer == "m0 draft"  # degraded to the top (lowest-index) surviving proposal
    assert any("synthesis" in msg for i, msg in res.failures if i == 0)
