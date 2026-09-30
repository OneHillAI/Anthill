"""PR #661 Tier 2: a single-model answer (no council used this turn) that hedges gets a natural-
language nudge toward the existing "go deeper" mechanism (intent.redo_mode), appended ONLY to what is
shown to the user - never to what is cached, published, or filed as a wiki page. A council-produced
answer never gets the nudge, even if its text happens to contain hedging language, since the council
already cross-checked it.
"""

import types
from typing import ClassVar

import anthill.council.engine as council_eng
from anthill.agent.intent import looks_uncertain
from anthill.wiki import ask as ask_mod
from anthill.wiki.ask import (
    _GO_DEEPER_SUGGESTION,
    _GO_DEEPER_SUGGESTION_WITH_PROVIDER,
    _should_suggest_deeper,
)
from anthill.wiki.workspace import Workspace

UNCERTAIN_ANSWER = "I'm not entirely sure, but the timeout appears to be 30 seconds."
CONFIDENT_ANSWER = "The timeout is 30 seconds."


class _FakeBackend:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def chat(self, messages, **_k):
        self.calls += 1
        return self._reply


class _RecordingCache:
    """Captures exactly what was passed to store(), so a test can assert the suggestion was never
    cached - a plain no-op stub (like test_ask_council.py's _no_cache) can't prove that."""

    instances: ClassVar[list["_RecordingCache"]] = []

    def __init__(self, **_k):
        self.stored: list[str] = []
        _RecordingCache.instances.append(self)

    def lookup(self, q):
        return None

    def store(self, q, a, slugs=None):
        self.stored.append(a)


def _cfg():
    return types.SimpleNamespace(org_council_members="[]")


def _noop_decrypt(s):
    return s


def _ws(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    return ws


def _mock_resolved(monkeypatch, n):
    resolved = [
        council_eng.ResolvedMember(
            index=i, backend=_FakeBackend(f"member-{i}"), provider="lambda", is_local=False
        )
        for i in range(n)
    ]
    monkeypatch.setattr(council_eng, "resolve_council_backends", lambda cfg, decrypt: resolved)
    return resolved


def _use_recording_cache(monkeypatch):
    _RecordingCache.instances = []
    monkeypatch.setattr(ask_mod, "SemanticCache", _RecordingCache)
    return _RecordingCache


# ── the detector + gate functions themselves ──────────────────────────────────────────────────────


def test_should_suggest_deeper_true_for_hedging():
    assert _should_suggest_deeper(UNCERTAIN_ANSWER)


def test_should_suggest_deeper_false_for_confident_answer():
    assert not _should_suggest_deeper(CONFIDENT_ANSWER)


def test_should_suggest_deeper_true_for_a_short_refusal():
    # a refusal is arguably the MOST uncertain outcome - go-deeper is a useful next step there too,
    # a deliberate design decision (not an accident of which regex happens to match first).
    assert _should_suggest_deeper("I don't have that information.")
    assert not looks_uncertain("I don't have that information.")  # confirms it's NOT via hedging


# ── ask(): single-model path ──────────────────────────────────────────────────────────────────────


def test_ask_appends_suggestion_for_an_uncertain_single_model_answer(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)  # no council configured
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert answer == UNCERTAIN_ANSWER + _GO_DEEPER_SUGGESTION


def test_ask_does_not_append_suggestion_for_a_confident_single_model_answer(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    backend = _FakeBackend(CONFIDENT_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert answer == CONFIDENT_ANSWER


def test_ask_never_caches_the_suggestion_text(tmp_path, monkeypatch):
    cache_cls = _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert _GO_DEEPER_SUGGESTION in answer  # the returned/shown value DOES carry it
    stored = [a for inst in cache_cls.instances for a in inst.stored]
    assert stored == [UNCERTAIN_ANSWER]  # what was cached does NOT


def test_ask_council_answer_is_never_suggested_even_if_it_hedges(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 2)
    council_answer = "I'm not entirely sure, but the council believes the timeout is 30 seconds."
    monkeypatch.setattr(
        council_eng,
        "run_council",
        lambda cfg, messages, decrypt, **k: council_eng.CouncilResult(
            answer=council_answer, synthesized=True
        ),
    )
    backend = _FakeBackend("should never be used")
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert answer == council_answer  # the council already cross-checked it - no extra nudge
    assert _GO_DEEPER_SUGGESTION not in answer


def test_ask_council_fallback_to_single_member_is_still_eligible_for_the_suggestion(
    tmp_path, monkeypatch
):
    # Council was CONFIGURED (2 members) but every member failed - ask() silently falls back to the
    # single backend. This proves the gate tracks whether a council actually ANSWERED, not merely
    # whether one was configured.
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 2)

    def _boom(cfg, messages, decrypt, **k):
        raise council_eng.AllMembersFailed(["member 0: boom", "member 1: boom"])

    monkeypatch.setattr(council_eng, "run_council", _boom)
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert answer == UNCERTAIN_ANSWER + _GO_DEEPER_SUGGESTION


def test_ask_image_turn_with_an_uncertain_answer_is_still_eligible(tmp_path, monkeypatch):
    # Regression guard: an image turn never assigns the `council` local variable at all (it never goes
    # through _council_answer). Before this PR's fix, referencing `council` here would have raised
    # UnboundLocalError for every image turn - this proves it does not, and that "no council was used"
    # is still correctly true for an image answer.
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 2)  # even with a council configured, image turns skip it entirely
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path),
        "what is in this picture?",
        backend,
        images_b64=["ZmFrZQ=="],
        cfg=_cfg(),
        decrypt=_noop_decrypt,
    )
    assert answer == UNCERTAIN_ANSWER + _GO_DEEPER_SUGGESTION


# ── #278: the suggestion names the connected backend when one exists ─────────────────────────────


def test_ask_offers_the_provider_clause_when_available(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path),
        "what's the timeout?",
        backend,
        cfg=_cfg(),
        decrypt=_noop_decrypt,
        provider_available=True,
    )
    assert answer == UNCERTAIN_ANSWER + _GO_DEEPER_SUGGESTION_WITH_PROVIDER
    assert "use the cloud model" in answer


def test_ask_omits_the_provider_clause_by_default(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    backend = _FakeBackend(UNCERTAIN_ANSWER)
    answer, _slugs, _hit = ask_mod.ask(
        _ws(tmp_path), "what's the timeout?", backend, cfg=_cfg(), decrypt=_noop_decrypt
    )
    assert answer == UNCERTAIN_ANSWER + _GO_DEEPER_SUGGESTION
    assert "cloud model" not in answer


# ── ask_stream(): single-model streaming path ─────────────────────────────────────────────────────


class _StreamBackend:
    def __init__(self, chunks):
        self._chunks = chunks

    def chat_stream(self, messages, **_k):
        yield from self._chunks


def test_ask_stream_yields_the_suggestion_as_a_final_chunk_when_uncertain(tmp_path, monkeypatch):
    cache_cls = _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    chunks = ["I'm not ", "entirely sure, ", "but the timeout appears to be 30 seconds."]
    out = list(
        ask_mod.ask_stream(
            _ws(tmp_path),
            "what's the timeout?",
            _StreamBackend(chunks),
            cfg=_cfg(),
            decrypt=_noop_decrypt,
        )
    )
    assert out[:-1] == chunks  # real token streaming, unchanged
    assert out[-1] == _GO_DEEPER_SUGGESTION
    stored = [a for inst in cache_cls.instances for a in inst.stored]
    assert stored == ["".join(chunks)]  # cached WITHOUT the suggestion


def test_ask_stream_appends_nothing_when_confident(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    chunks = ["The ", "timeout ", "is 30 seconds."]
    out = list(
        ask_mod.ask_stream(
            _ws(tmp_path),
            "what's the timeout?",
            _StreamBackend(chunks),
            cfg=_cfg(),
            decrypt=_noop_decrypt,
        )
    )
    assert out == chunks  # nothing extra appended


def test_ask_stream_offers_the_provider_clause_when_available(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 0)
    chunks = ["I'm not ", "entirely sure, ", "but the timeout appears to be 30 seconds."]
    out = list(
        ask_mod.ask_stream(
            _ws(tmp_path),
            "what's the timeout?",
            _StreamBackend(chunks),
            cfg=_cfg(),
            decrypt=_noop_decrypt,
            provider_available=True,
        )
    )
    assert out[-1] == _GO_DEEPER_SUGGESTION_WITH_PROVIDER


def test_ask_stream_council_delegation_is_never_suggested(tmp_path, monkeypatch):
    _use_recording_cache(monkeypatch)
    _mock_resolved(monkeypatch, 2)
    council_answer = "I'm not entirely sure, but the council believes it's 30 seconds."
    monkeypatch.setattr(
        council_eng,
        "run_council",
        lambda cfg, messages, decrypt, **k: council_eng.CouncilResult(
            answer=council_answer, synthesized=True
        ),
    )

    class _NeverStreamed(_StreamBackend):
        def chat_stream(self, messages, **_k):  # must never be reached - delegates to ask()
            raise AssertionError("ask_stream must delegate to ask() when the council is active")

        def chat(self, messages, **_k):
            return "should not be used either"

    out = list(
        ask_mod.ask_stream(
            _ws(tmp_path),
            "what's the timeout?",
            _NeverStreamed([]),
            cfg=_cfg(),
            decrypt=_noop_decrypt,
        )
    )
    assert out == [council_answer]  # exactly the council's own answer, no suggestion appended
