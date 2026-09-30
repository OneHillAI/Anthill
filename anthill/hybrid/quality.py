from __future__ import annotations

import re
from dataclasses import dataclass, field

# Phrases that signal the local model is uncertain or admitting a gap.
_HEDGE_PATTERNS = [
    r"\bi (don'?t|do not) know\b",
    r"\bi'?m not sure\b",
    r"\bi am not sure\b",
    r"\bi (can'?t|cannot) (help|answer|determine|find)\b",
    r"\bno (relevant )?information\b",
    r"\b(the )?wiki (does(n'?t| not)|doesn'?t) (cover|contain|have|mention)\b",
    r"\boutside (the|this) wiki\b",
    r"\bnot (covered|mentioned|available|found) (in|on)\b",
    r"\bunable to\b",
    r"\binsufficient (information|context|data)\b",
    r"\bi have no\b",
    r"\bunclear\b",
    r"\bcould not find\b",
]
_HEDGE_RE = re.compile("|".join(_HEDGE_PATTERNS), re.I)


@dataclass
class QualityVerdict:
    confidence: float  # 0.0 (very weak) .. 1.0 (strong)
    sufficient: bool  # confidence >= threshold
    reasons: list[str] = field(default_factory=list)


def assess(answer: str, question: str, *, threshold: float = 0.5) -> QualityVerdict:
    """Cheap, deterministic confidence estimate for a locally-generated answer.

    This is the gate that decides whether to escalate to a cloud model. It is
    intentionally conservative and explainable - no extra model call. Signals:
      - hedging / gap-admitting phrases (strong negative)
      - very short answers to non-trivial questions
      - answers that merely restate the question
      - answers that don't reference any content word from the question

    threshold is user-defined: a higher threshold escalates more often.
    """
    reasons: list[str] = []
    score = 1.0
    text = answer.strip()

    if not text:
        return QualityVerdict(0.0, False, ["empty answer"])

    hedges = _HEDGE_RE.findall(text)
    if hedges:
        score -= 0.6
        reasons.append(f"hedging/gap language ({len(hedges)} marker(s))")

    words = text.split()
    if len(words) < 12 and len(question.split()) >= 6:
        score -= 0.3
        reasons.append(f"very short answer ({len(words)} words)")

    # Overlap between question content words and the answer.
    q_words = _content_words(question)
    if q_words:
        a_lower = text.lower()
        overlap = sum(1 for w in q_words if w in a_lower) / len(q_words)
        if overlap < 0.25:
            score -= 0.25
            reasons.append("answer barely references the question's topic")

    # Restating the question rather than answering it.
    if text.lower().rstrip("?.") == question.lower().rstrip("?."):
        score -= 0.5
        reasons.append("answer restates the question")

    score = max(0.0, min(1.0, score))
    return QualityVerdict(
        confidence=round(score, 3),
        sufficient=score >= threshold,
        reasons=reasons or ["no weakness signals detected"],
    )


def _content_words(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "that",
        "this",
        "with",
        "from",
        "what",
        "which",
        "how",
        "when",
        "who",
        "why",
        "can",
        "could",
        "would",
        "should",
        "did",
        "about",
        "any",
        "all",
        "not",
        "but",
        "our",
        "your",
        "their",
        "its",
        "into",
        "over",
        "more",
        "most",
    }
    return {w for w in re.split(r"\W+", text.lower()) if len(w) > 2} - stop
