"""The verifier core: deterministic checks + an optional different-family model cross-check.

``verify(output, kind=..., ...)`` returns a ``Verdict``. Deterministic, model-free checks run first
(cheap, reliable). Then, if a model of a DIFFERENT FAMILY than the producer is available, a second
model sanity-checks the output. The confidence gating here is a conservative pre-calibration heuristic:
anything short of "deterministic-clean AND an independent model agrees" is marked ``needs_review`` so
the user is pulled in. The auto-pass THRESHOLD per kind is meant to come from the eval's calibration
(qa/chat-eval/calibrate.py); until that certifies a kind, keep everything advisory (surface it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("wiki_write", "task_result", "action")

# Common words ignored when checking whether an output covers the goal/source key terms.
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "as",
        "at",
        "by",
        "we",
        "you",
        "i",
        "our",
        "your",
        "their",
        "them",
        "they",
        "he",
        "she",
        "his",
        "her",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "into",
        "about",
        "over",
        "under",
        "than",
        "then",
        "so",
        "if",
        "not",
        "no",
        "yes",
    ]
)


def _terms(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", (text or "").lower()) if len(t) > 2 and t not in _STOP}


@dataclass
class CrossCheck:
    """The independent (different-family) model's verdict. ``ok=None`` = no verifier model available."""

    ok: bool | None
    reason: str = ""
    model: str = ""


@dataclass
class Verdict:
    ok: bool
    confidence: float  # 0..1
    reason: str
    kind: str = ""
    checks: list = field(default_factory=list)  # [(name, passed, detail)]
    needs_review: bool = True  # surface to the user unless high-confidence-clean
    crosscheck_model: str = ""


# ── deterministic checks (model-free, run first) ──────────────────────────────


# Outputs at or below this many words are treated as generative/terse (a note, a tip, a one-line
# answer) and exempted from the goal-term overlap gate: good generative work legitimately shares few
# of the goal's literal words, so overlap says nothing about correctness there (issue #393).
_GOAL_MATCH_SHORT_WORDS = 40


def _goal_covered(output: str, goal: str) -> tuple[str, bool, str]:
    """Weak, high-precision signal that the output isn't grossly off-goal. Literal goal-term overlap
    can't judge generative work (a note/tip/summary in fresh wording shares few of the goal's words),
    so this only fails a **longer** output that echoes **none** of a substantive goal - a plausible
    'wandered off entirely' case. Short generative outputs are exempt, and semantic goal-satisfaction
    is judged by the different-family model cross-check, not here (issue #393)."""
    g = _terms(goal)
    if not g:
        return ("goal_match", True, "no goal terms to match")
    words = (output or "").split()
    if not words:
        # An empty result is a failure, not a "short generative output" - don't exempt it (the
        # nonempty check also catches this; being explicit avoids goal_match reading as a pass).
        return ("goal_match", False, "empty output")
    o = _terms(output)
    hit = sum(1 for t in g if t in o)
    if len(words) <= _GOAL_MATCH_SHORT_WORDS:
        return ("goal_match", True, f"{hit}/{len(g)} goal terms (short output; overlap not gated)")
    # Longer output: only a total disconnect (zero overlap with a substantive goal) trips the gate.
    return ("goal_match", hit >= 1, f"{hit}/{len(g)} goal terms present")


def _faithful_overlap(output: str, source: str) -> tuple[str, bool, str]:
    """Cheap anti-fabrication signal for a wiki write: most of the page's substantive terms should
    appear in the source it claims to summarise. A page inventing facts not in the source scores low.
    (This is a heuristic gate; the model cross-check does the real faithfulness judgement.)"""
    o = _terms(output)
    if not o:
        return ("faithful_overlap", False, "empty output")
    s = _terms(source)
    if not s:
        return ("faithful_overlap", True, "no source to check against")
    grounded = sum(1 for t in o if t in s)
    frac = grounded / len(o)
    return ("faithful_overlap", frac >= 0.35, f"{frac * 100:.0f}% of output terms found in source")


def _cost_zero(meta: dict) -> tuple[str, bool, str]:
    """A local, in-perimeter output should not have incurred cloud spend."""
    cost = float((meta or {}).get("cloud_cost_usd", 0) or 0)
    return ("cost_zero", cost == 0.0, f"cloud_cost_usd={cost}")


def _deterministic(output: str, *, kind: str, source: str, goal: str, meta: dict) -> list:
    checks = [("nonempty", bool((output or "").strip()), "")]
    if kind in ("task_result", "action") and goal:
        checks.append(_goal_covered(output, goal))
    if kind == "wiki_write" and source:
        checks.append(_faithful_overlap(output, source))
    checks.append(_cost_zero(meta))
    return checks


# ── different-family model selection + default cross-check ────────────────────


# Free-RAM floor below which the model cross-check is skipped rather than load a second model
# concurrently with the producer (issue #413). Roughly a mid-size local model's footprint.
_VERIFIER_MIN_FREE_GB = 6.0


def pick_verifier_model(producer_model: str, installed: set[str]) -> str | None:
    """An installed model of a DIFFERENT FAMILY than the producer (never self-check), preferring a
    non-tiny one. Returns None when only the producer's family is installed - the caller then falls
    back to deterministic-only + always-surface (a single-model box can't cross-check itself)."""
    fam = (producer_model or "").split(":")[0]
    others = sorted(t for t in installed if t.split(":")[0] != fam)
    if not others:
        return None
    # prefer a capable cross-checker: deprioritise the tiny FAST models (…:3b / :1b).
    ranked = sorted(others, key=lambda t: (bool(re.search(r":(1|3)b$", t)), t))
    return ranked[0]


def default_crosscheck(ollama_url: str, producer_model: str, installed: set[str]):
    """Build a cross-check callable that asks a different-family local model whether the output is
    sound. Returns a function ``(output, *, kind, source, goal, context) -> CrossCheck``. If no
    different-family model is installed, the function always returns ``CrossCheck(ok=None)``."""
    model = pick_verifier_model(producer_model, installed)

    def _run(
        output: str, *, kind: str, source: str = "", goal: str = "", context: str = ""
    ) -> CrossCheck:
        if not model:
            return CrossCheck(ok=None, reason="no different-family verifier model installed")
        # Memory-pressure guard (#413): the cross-check loads a SECOND (different-family) model while the
        # producer model is still resident. On a memory-constrained Mac that concurrency saturates unified
        # memory and freezes the app. Skip the model cross-check when free RAM is below the floor - the
        # deterministic checks still run and the verdict fails safe to needs_review (ok=None), never blocks.
        from ..hosting.sizing import free_mem_gb

        free = free_mem_gb()
        if free is not None and free < _VERIFIER_MIN_FREE_GB:
            return CrossCheck(
                ok=None,
                model=model,
                reason=f"skipped: only {free:.1f} GB free - not loading a 2nd model under memory pressure",
            )
        from ..common.jsonchat import extract_json, json_chat
        from ..inference.base import Message
        from ..inference.ollama import OllamaBackend

        crit = {
            "wiki_write": "The wiki page is faithful to its SOURCE and invents no facts not supported by it.",
            "task_result": "The result actually satisfies the stated GOAL of the task.",
            "action": "The action is consistent with the user's GOAL and scoped correctly.",
        }.get(kind, "The output is correct and appropriate.")
        sys = (
            "You independently check another model's output. Judge ONLY the criterion. Return ONLY "
            'JSON: {"ok": true or false, "reason": "<one short sentence>"}.\n' + crit
        )
        parts = [f"KIND: {kind}"]
        if goal:
            parts.append(f"GOAL: {goal}")
        if source:
            parts.append(f"SOURCE:\n{source[:4000]}")
        if context:
            parts.append(f"CONTEXT: {context[:1000]}")
        parts.append(f"OUTPUT:\n{output[:4000]}")
        try:
            backend = OllamaBackend(ollama_url, model)
            data = extract_json(
                json_chat(backend, [Message("system", sys), Message("user", "\n\n".join(parts))])
            )
        except Exception:
            return CrossCheck(ok=None, reason="verifier model unavailable", model=model)
        if not isinstance(data, dict) or "ok" not in data:
            return CrossCheck(ok=None, reason="verifier returned no verdict", model=model)
        return CrossCheck(
            ok=bool(data.get("ok")), reason=str(data.get("reason", ""))[:200], model=model
        )

    return _run


def crosscheck_for(ollama_url: str, producer_model: str):
    """Convenience: build a :func:`default_crosscheck` for a producer model, discovering the installed
    models best-effort. If the local model list can't be read (Ollama down, single-box offline), no
    different-family model is assumed - the cross-check reports ``ok=None`` and the verdict falls back
    to deterministic-only + always-surface. Never raises."""
    installed: set[str] = set()
    try:
        from ..inference.ollama import OllamaBackend

        installed = set(OllamaBackend(ollama_url, producer_model).installed_models())
    except Exception:
        installed = set()
    return default_crosscheck(ollama_url, producer_model, installed)


# ── verify() ──────────────────────────────────────────────────────────────────


def verify(
    output: str,
    *,
    kind: str,
    source: str = "",
    goal: str = "",
    context: str = "",
    meta: dict | None = None,
    crosscheck=None,
) -> Verdict:
    """Verify a consequential agent ``output`` of ``kind`` in {wiki_write, task_result, action}.

    ``crosscheck`` is an optional independent-model callable ``(output, *, kind, source, goal,
    context) -> CrossCheck`` (build one with :func:`default_crosscheck`). When it is absent or reports
    ``ok=None`` (no different-family model), the verdict falls back to deterministic-only and is always
    surfaced for review - a single-model install can't cross-check itself.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    det = _deterministic(output, kind=kind, source=source, goal=goal, meta=meta or {})
    det_ok = all(p for _, p, _ in det)
    det_fail = next((f"{n}: {d}" for n, p, d in det if not p), "")

    cc = None
    if crosscheck is not None:
        try:
            cc = crosscheck(output, kind=kind, source=source, goal=goal, context=context)
        except Exception:
            cc = CrossCheck(ok=None, reason="cross-check raised")

    checks = list(det)
    if cc is not None:
        checks.append(("crosscheck", cc.ok if cc.ok is not None else False, cc.reason))

    # Conservative pre-calibration gating. A kind is only trusted to auto-pass (needs_review=False)
    # once BOTH the deterministic checks are clean AND an independent model agrees. Everything else -
    # a failed check, a disagreeing verifier, or no verifier available - surfaces to the user.
    if not det_ok:
        return Verdict(
            False,
            0.9,
            f"deterministic check failed ({det_fail})",
            kind,
            checks,
            True,
            cc.model if cc else "",
        )
    if cc is None or cc.ok is None:
        return Verdict(
            True,
            0.5,
            "deterministic checks passed; no independent verifier available - surfacing for review",
            kind,
            checks,
            True,
            cc.model if cc else "",
        )
    if cc.ok:
        return Verdict(
            True,
            0.85,
            cc.reason or "deterministic checks passed and the independent verifier agrees",
            kind,
            checks,
            False,
            cc.model,
        )
    return Verdict(
        False, 0.6, f"independent verifier disagrees: {cc.reason}", kind, checks, True, cc.model
    )
