"""Per-model input-context budgets (issue #277).

Running-message compaction (agent/compact.py, agent/executor.py) and chat-history trimming
(wiki/history.py) used fixed budgets (6000 tokens / 24000 chars), which capped a large-context model
(say a 128k-window one) to a fraction of what it can hold. These derive the budget from the model's
actual context window, floored at the historical values so a small- or unknown-window model never
regresses.
"""

from __future__ import annotations

_DEFAULT_WINDOW = 8192  # conservative fallback when a model's context length can't be read
_INPUT_FRACTION = (
    0.6  # spend at most this share of the window on INPUT, leaving headroom for output
)
_MIN_TOKENS = 6000  # historical floor (the old hardcoded budget)
_CHARS_PER_TOKEN = 4  # rough average


def window_for(backend, model: str | None = None) -> int:
    """The model's context window in tokens (best-effort): the backend's ``context_window()`` probe when
    it exposes one and it returns a positive value, else a safe default. Never raises."""
    fn = getattr(backend, "context_window", None)
    if callable(fn):
        try:
            w = fn(model)
            if isinstance(w, int) and w > 0:
                return w
        except Exception:
            pass
    return _DEFAULT_WINDOW


def known_window(backend, model: str | None = None) -> int:
    """The model's window when the backend really reports one, else 0. Unlike ``window_for`` this never
    guesses: a backend that does not know its window (a cloud or OpenAI-compatible endpoint) gets 0, so
    the prompt fit guard (``inference/fit.py``) leaves its prompts alone instead of cutting them to a
    default."""
    fn = getattr(backend, "context_window", None)
    if callable(fn):
        try:
            w = fn(model)
            if isinstance(w, int) and w > 0:
                return w
        except Exception:
            pass
    return 0


def token_budget(backend, model: str | None = None) -> int:
    """Tokens to spend on INPUT context - a fraction of the window, never below the historical floor."""
    return max(_MIN_TOKENS, int(window_for(backend, model) * _INPUT_FRACTION))


def char_budget(backend, model: str | None = None) -> int:
    """Character budget for history trimming (~4 chars/token), never below the historical floor."""
    return max(_MIN_TOKENS * _CHARS_PER_TOKEN, token_budget(backend, model) * _CHARS_PER_TOKEN)
