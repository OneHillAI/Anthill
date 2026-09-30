"""Runtime cross-check verifier core (anthill/verify). Deterministic checks + the different-family
model cross-check combine into a Verdict; advisory by default (surface unless clean AND an independent
model agrees). Spec: engineering-plans/RUNTIME_CROSSCHECK_VERIFIER.md."""

import pytest

from anthill.verify import CrossCheck, Verdict, pick_verifier_model, verify


def _agree(*a, **k):
    return CrossCheck(ok=True, reason="looks sound", model="mistral-nemo:12b")


def _disagree(*a, **k):
    return CrossCheck(
        ok=False, reason="fabricates a figure not in the source", model="mistral-nemo:12b"
    )


def _unavailable(*a, **k):
    return CrossCheck(ok=None, reason="no different-family verifier model installed")


# ── verify(): combine logic ────────────────────────────────────────────────────


def test_bad_kind_raises():
    with pytest.raises(ValueError):
        verify("x", kind="not_a_kind")


def test_empty_output_fails_deterministically_and_surfaces():
    v = verify("", kind="task_result", goal="summarise this week's deploys")
    assert v.ok is False and v.needs_review is True
    assert any(n == "nonempty" and not p for n, p, _ in v.checks)


def test_long_output_that_totally_misses_the_goal_is_flagged():
    # A longer output that echoes NONE of a substantive goal still trips the deterministic gate.
    v = verify(
        "The garden looked lovely in the morning light, with dew on every leaf and birds calling "
        "from the old oak. I walked slowly along the path, admiring the roses, and thought about "
        "nothing in particular except how pleasant a quiet start to any given day can feel.",
        kind="task_result",
        goal="summarise this week's production deploys and incidents",
    )
    assert v.ok is False and v.needs_review is True
    assert any(n == "goal_match" and not p for n, p, _ in v.checks)


def test_short_generative_output_is_not_hard_failed():
    # issue #393: a valid short generative output legitimately shares few of the goal's literal
    # terms; it must NOT be marked a high-confidence deterministic failure. With no independent
    # model it is surfaced (advisory 0.5), and with one agreeing it auto-passes.
    out = "Tip: Always communicate your progress and blockers to keep everyone aligned."
    goal = "Write one sentence with a practical tip for effective teamwork."
    v = verify(out, kind="task_result", goal=goal, crosscheck=_unavailable)
    assert v.ok is True and v.confidence == 0.5  # surfaced, not a 0.90 deterministic failure
    assert all(p for n, p, _ in v.checks if n == "goal_match")  # goal_match did not fail
    v2 = verify(out, kind="task_result", goal=goal, crosscheck=_agree)
    assert v2.ok is True and v2.needs_review is False  # model agrees -> auto-pass


def test_empty_output_is_not_exempted_by_the_short_rule():
    # The short-output exemption must not let an empty result pass goal_match (ASDD review on #393).
    v = verify("", kind="task_result", goal="write a one-sentence teamwork tip")
    assert v.ok is False and v.needs_review is True
    assert any(n == "goal_match" and not p for n, p, _ in v.checks)


def test_task_result_that_matches_the_goal_passes_deterministically():
    v = verify(
        "This week's production deploys: 4 releases, 1 incident, all resolved.",
        kind="task_result",
        goal="summarise this week's production deploys and incidents",
    )
    assert all(p for _, p, _ in v.checks)  # deterministic clean


def test_no_verifier_available_passes_low_confidence_and_surfaces():
    # deterministic-clean but no independent model -> advisory: surface for review, confidence 0.5
    v = verify(
        "This week's deploys: 4 releases, 1 incident.",
        kind="task_result",
        goal="summarise this week's deploys and incidents",
        crosscheck=_unavailable,
    )
    assert v.ok is True and v.needs_review is True and v.confidence == 0.5


def test_crosscheck_agreement_allows_auto_pass():
    v = verify(
        "This week's deploys: 4 releases, 1 incident.",
        kind="task_result",
        goal="summarise this week's deploys and incidents",
        crosscheck=_agree,
    )
    assert v.ok is True and v.needs_review is False and v.confidence >= 0.8
    assert v.crosscheck_model == "mistral-nemo:12b"


def test_crosscheck_disagreement_surfaces_even_when_deterministic_clean():
    v = verify(
        "Revenue tripled to $9M this quarter.",
        kind="wiki_write",
        source="Revenue rose to about 9 million dollars this quarter.",
        crosscheck=_disagree,
    )
    # even though the faithfulness overlap is high, the independent model's disagreement wins
    assert v.ok is False and v.needs_review is True


def test_wiki_write_faithfulness_overlap_catches_fabrication():
    # a page whose terms are almost entirely absent from the source scores low on the cheap gate
    v = verify(
        "Quantum tunnelling powers the new perpetual motion engine.",
        kind="wiki_write",
        source="The refund window is 45 days for hardware purchases.",
    )
    assert any(n == "faithful_overlap" and not p for n, p, _ in v.checks)
    assert v.ok is False


def test_cost_nonzero_fails():
    v = verify(
        "A fine answer.", kind="task_result", goal="a fine answer", meta={"cloud_cost_usd": 0.004}
    )
    assert any(n == "cost_zero" and not p for n, p, _ in v.checks) and v.ok is False


# ── pick_verifier_model: different family, never self-check ─────────────────────


def test_pick_verifier_model_prefers_a_different_family():
    tag = pick_verifier_model("qwen3:8b", {"qwen3:8b", "mistral-nemo:12b", "qwen2.5:3b"})
    assert tag == "mistral-nemo:12b"  # different family; the qwen* are the producer's family


def test_pick_verifier_model_none_when_only_producer_family():
    # only qwen3:* installed (same base as the producer) -> nothing to cross-check with
    assert pick_verifier_model("qwen3:8b", {"qwen3:8b", "qwen3:14b"}) is None


def test_pick_verifier_model_deprioritises_tiny_models():
    # both are a different family; prefer the non-tiny one
    tag = pick_verifier_model("qwen3:8b", {"qwen3:8b", "llama3.2:3b", "mistral-nemo:12b"})
    assert tag == "mistral-nemo:12b"


def test_verdict_is_a_dataclass_with_the_spec_fields():
    v = verify("ok", kind="action", goal="ok")
    assert isinstance(v, Verdict)
    for f in ("ok", "confidence", "reason", "kind", "checks", "needs_review"):
        assert hasattr(v, f)
