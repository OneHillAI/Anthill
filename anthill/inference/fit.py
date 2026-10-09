"""Fit a prompt into the model's window instead of letting the engine drop pieces of it (issue #109).

When a prompt is longer than the window, Ollama drops the OLDEST whole messages. After the system message,
the oldest message of a wiki answer is the reference block, so a long page silently vanished and the model
answered without it. Here the prompt is shortened on purpose, in a fixed order:

1. The system message and the question are never touched.
2. The reference is cut from its END (so what comes first, such as the profile, the principles and the
   best-ranked page, survives) down to a floor of about 1,500 tokens.
3. Older conversation turns are dropped, oldest first. A leading conversation summary (which costs a model call
   to make) is kept until every other older turn has gone.
4. Only if that is still too long, the reference goes below its floor.

A prompt that already fits is returned unchanged, byte for byte. Sizes are estimates, deliberately low-biased so
the real count stays under the window: 3 characters per token for Latin-script text, and more tokens per
character for other scripts (see ``cost``). About 2,048 tokens of the window are kept free for the answer, which
is less than the longest answer allowed (4,096 tokens), so a very long answer can still push the start of the
prompt out of the window while it is being written.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 3  # a cautious estimate for Latin-script text: real text averages 3.5 to 4
ANSWER_RESERVE_TOKENS = 2048  # kept free in the window for the answer itself
REFERENCE_FLOOR_TOKENS = 1500  # the reference is cut to this before any history is dropped
SUMMARY_PREFIX = (
    "Earlier in this conversation (summary)"  # starts the summary turn made by wiki/history.py
)


def _unit(ch: str) -> int:
    """The cost of one character, where a Latin-script character is 1 (a third of a token)."""
    o = ord(ch)
    if o < 0x370:
        return 1  # Latin, digits, punctuation
    if 0xE00 <= o <= 0xEFF or 0x1000 <= o <= 0x109F or 0x1780 <= o <= 0x17FF or o >= 0x2E80:
        return 3  # Thai, Lao, Myanmar, Khmer, CJK, kana, Hangul: about a token a character
    return 2  # Greek, Cyrillic, Arabic, Hebrew, Indic and other scripts


def cost(text: str) -> int:
    """The size of ``text`` in thirds of a token: 1 per Latin-script character, more for other scripts."""
    if text.isascii():
        return len(text)
    return sum(_unit(c) for c in text)


def estimate_tokens(text: str) -> int:
    """A rough token count for ``text``, rounded up (see ``cost``)."""
    return -(-cost(text) // CHARS_PER_TOKEN)


def _cut(text: str, limit: int) -> str:
    """The longest start of ``text`` that costs at most ``limit``, backed up to a paragraph break when close."""
    if limit <= 0:
        return ""
    if cost(text) <= limit:
        return text
    spent, end = 0, 0
    for i, ch in enumerate(text):
        spent += _unit(ch)
        if spent > limit:
            break
        end = i + 1
    head = text[:end]
    cut = head.rfind("\n\n")
    if cut >= int(len(head) * 0.85):  # only when it costs little, so a short budget is not wasted
        head = head[:cut]
    return head.rstrip()


def fit_prompt(
    *,
    fixed_cost: int,
    reference: str,
    history: list[tuple[str, str]],
    window: int,
    reserve_tokens: int = ANSWER_RESERVE_TOKENS,
    floor_tokens: int = REFERENCE_FLOOR_TOKENS,
) -> tuple[str, list[tuple[str, str]]]:
    """Shorten ``reference`` and ``history`` so ``fixed_cost`` + both fit in ``window`` minus the answer reserve.

    ``fixed_cost`` is the ``cost`` of everything that must stay (system message, question, framing text).
    Returns the (possibly shorter) reference and history. When nothing needs shortening the SAME objects come
    back. A ``window`` of 0 or less means the window is unknown, so nothing is changed.
    """
    if window <= 0:
        return reference, history
    budget = (window - reserve_tokens) * CHARS_PER_TOKEN - fixed_cost
    ref_cost = cost(reference)
    hist_costs = [cost(c) for _, c in history]
    if ref_cost + sum(hist_costs) <= budget:
        return reference, history
    floor = floor_tokens * CHARS_PER_TOKEN
    # 2. Cut the reference, but not below its floor while history can still give way.
    allowed = max(floor, budget - sum(hist_costs))
    if ref_cost > allowed:
        reference = _cut(reference, allowed)
        ref_cost = cost(reference)
    # 3. Drop the oldest turns until it fits, keeping a leading summary for last.
    kept = list(history)
    costs = list(hist_costs)
    while kept and ref_cost + sum(costs) > budget:
        drop = 1 if len(kept) > 1 and kept[0][1].startswith(SUMMARY_PREFIX) else 0
        kept.pop(drop)
        costs.pop(drop)
    # 4. Last resort: the reference below its floor, down to whatever the window leaves.
    if ref_cost > budget:
        reference = _cut(reference, budget)
    return reference, kept
