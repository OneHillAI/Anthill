# Spec: The verifier's goal-match check stops over-flagging generative outputs

Status: implemented. Lane: `pillar:model`. Issue: #393.

## Problem

The runtime cross-check verifier runs deterministic checks before the model cross-check, and **any**
failed deterministic check short-circuits to a hard `Verdict(ok=False, confidence=0.90,
needs_review=True)`. One of those checks, `_goal_covered`, counted how many of the goal's literal terms
appear in the output and failed it below `hit >= len(goal_terms) // 2`.

Good **generative** work (a note, a tip, a summary written in fresh wording) legitimately shares few of
the goal's literal words, so it scored low and was hard-failed - before the semantic model cross-check
even ran. Observed live (v0.10.4): a Task ("write a two-sentence motivational note") and an Agent
("write one sentence with a practical tip for teamwork") both produced correct, on-goal outputs that
were flagged "needs review - deterministic check failed (goal_match: 1/7 goal terms present)". Result:
near-universal false positives on generative tasks/agents, alert fatigue, and users learning to ignore
the badge.

## Policy

Literal goal-term overlap is a weak signal for generative work, so `_goal_covered` becomes a narrow,
high-precision tripwire instead of a strict gate:

- **Short outputs are exempt.** An output at or below `_GOAL_MATCH_SHORT_WORDS` (40) words is treated as
  generative/terse and passes the goal-match check regardless of overlap (the real overlap is still
  reported in the check detail).
- **Longer outputs fail only on a total disconnect.** Above the threshold, the check fails only when the
  output echoes **zero** of a substantive goal's terms - a plausible "wandered off entirely" case.
- **Semantic goal-satisfaction is judged by the model cross-check**, not this heuristic. When a
  different-family model is available it decides; when none is (single-model box), the verdict already
  falls back to "surface for review" at confidence 0.5 - advisory, not a high-confidence failure.

Net effect: a valid generative output is no longer hard-failed by a crude term-overlap heuristic. It
either auto-passes when an independent model agrees, or is surfaced mildly (0.5) when no verifier is
available - never marked a 0.90 deterministic failure on overlap alone.

## Acceptance criteria

- A short valid generative output (e.g. a one-sentence tip) with a low goal-term overlap is not a
  deterministic failure: with no verifier it is surfaced at confidence 0.5; with an agreeing model it
  auto-passes (`needs_review=False`).
- A longer output that echoes none of a substantive goal still fails the goal-match check.
- The goal-match check detail continues to report the real overlap (`hit/total goal terms`).
- Both task and agent result hooks benefit (they share `verify()`); covered by `tests/test_verify.py`
  and `tests/test_verify_task_results.py`.
