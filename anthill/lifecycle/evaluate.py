from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..inference.base import Message

# Fixed seed so, when there are more held-out examples than `sample`, the scored subset is an
# order-independent (not biased to the first N) yet reproducible draw.
_EVAL_SAMPLE_SEED = 1729


@dataclass
class EvalResult:
    model_a: str
    model_b: str
    n: int
    score_a: float  # mean cosine similarity of A's answers to the reference
    score_b: float
    wins_a: int  # examples where A scored higher
    wins_b: int
    ties: int
    per_example: list[dict] = field(default_factory=list)

    @property
    def winner(self) -> str:
        if abs(self.score_a - self.score_b) < 0.01:
            return "tie"
        return self.model_a if self.score_a > self.score_b else self.model_b

    def summary(self) -> str:
        return (
            f"{self.model_a}: {self.score_a:.3f} ({self.wins_a} wins)  vs  "
            f"{self.model_b}: {self.score_b:.3f} ({self.wins_b} wins)  "
            f"[{self.ties} ties, n={self.n}]  →  winner: {self.winner}"
        )


def evaluate_models(
    backend,  # OllamaBackend (supports model= override)
    model_a: str,
    model_b: str,
    examples: list[tuple[str, str]],  # (instruction, reference_answer)
    *,
    sample: int = 20,
) -> EvalResult:
    """Score two models against reference answers from the org's gold examples.

    Reference answers are the org's known-good outputs (admin-approved or
    thumbs-up); the training promotion gate passes a held-out slice the
    candidate never trained on. Each candidate's answer is embedded and compared to the
    reference by cosine similarity - higher means closer to what the org
    already validated as correct. This is a regression check before switching
    the default model, not an absolute quality benchmark.
    """
    from ..cache import embedder as emb

    # Score a subset of at most `sample`. When more held-out examples are available than `sample`,
    # draw them deterministically at random rather than taking the first N (which would bias the
    # gate toward whatever ordering the split produced).
    if len(examples) > sample:
        pairs = random.Random(_EVAL_SAMPLE_SEED).sample(examples, sample)
    else:
        pairs = list(examples)
    if not pairs:
        return EvalResult(model_a, model_b, 0, 0.0, 0.0, 0, 0, 0)

    sims_a: list[float] = []
    sims_b: list[float] = []
    wins_a = wins_b = ties = 0
    per: list[dict] = []

    for instruction, reference in pairs:
        ref_vec = emb.embed(reference)
        msgs = [Message("user", instruction)]

        ans_a = backend.chat(msgs, model=model_a)
        ans_b = backend.chat(msgs, model=model_b)

        sa = emb.cosine(ref_vec, emb.embed(ans_a))
        sb = emb.cosine(ref_vec, emb.embed(ans_b))
        sims_a.append(sa)
        sims_b.append(sb)

        if abs(sa - sb) < 0.01:
            ties += 1
        elif sa > sb:
            wins_a += 1
        else:
            wins_b += 1

        per.append(
            {"instruction": instruction[:80], "score_a": round(sa, 3), "score_b": round(sb, 3)}
        )

    return EvalResult(
        model_a=model_a,
        model_b=model_b,
        n=len(pairs),
        score_a=round(sum(sims_a) / len(sims_a), 4),
        score_b=round(sum(sims_b) / len(sims_b), 4),
        wins_a=wins_a,
        wins_b=wins_b,
        ties=ties,
        per_example=per,
    )
