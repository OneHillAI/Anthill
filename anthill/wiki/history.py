"""Fit prior conversation turns into the model's budget.

The chat handler used to fetch the last turns and throw them away, so the model had no memory of
the conversation (it would re-ask things the user already answered, and could not refer back to the
opening question). This passes the real history in - but a long chat can exceed the model's context,
so when the turns get heavy we keep the most recent ones verbatim and condense everything older into
a single short summary note. The budget is a deliberate proxy for the context window (roughly four
characters per token); the summariser is injected so this is unit-testable without a model.
"""

from __future__ import annotations

from collections.abc import Callable

Turn = tuple[str, str]  # (role, content) where role is "user" | "assistant" | "system"


def _chars(turns: list[Turn]) -> int:
    return sum(len(c) for _, c in turns)


def prepare_history(
    turns: list[Turn],
    *,
    budget_chars: int = 24000,
    keep_last: int = 6,
    summarize: Callable[[str], str] | None = None,
) -> list[Turn]:
    """Return prior turns trimmed to ``budget_chars``.

    ``turns`` are the earlier (role, content) messages in order, EXCLUDING the current question.
    Under budget, they pass through unchanged. Over budget, the last ``keep_last`` turns are kept
    verbatim and everything older is condensed into one leading ``system`` summary note via
    ``summarize`` (the model). If no summariser is given or it fails, fall back to dropping the
    oldest turns until the rest fit - memory degrades gracefully rather than breaking the answer.
    """
    turns = [(r, c) for r, c in turns if c and c.strip()]
    if not turns or _chars(turns) <= budget_chars:
        return turns  # comfortably within the context budget: pass through verbatim

    recent = turns[-keep_last:] if keep_last > 0 else []
    older = turns[: len(turns) - len(recent)]
    if not older:
        return recent

    digest = ""
    if summarize is not None:
        transcript = "\n".join(f"{r}: {c}" for r, c in older)
        try:
            digest = (summarize(transcript) or "").strip()
        except Exception:
            digest = ""

    if digest:
        note: Turn = ("system", f"Earlier in this conversation (summary): {digest}")
        return [note, *recent]

    # No usable summary: keep as many recent turns as the budget allows (newest first).
    kept: list[Turn] = []
    for turn in reversed(turns):
        if kept and _chars([turn, *kept]) > budget_chars:
            break
        kept.insert(0, turn)
    return kept
