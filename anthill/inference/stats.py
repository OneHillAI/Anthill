"""Timing and token counts for one model call (#219).

A ``TurnStats`` holds numbers only: no prompt, no answer, no wiki text. Every figure is ``None`` when the
engine did not report it; ``None`` never means 0, so a missing counter cannot read as "nothing was cached".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# The fields that are stored with a metric row (``query_metrics``): the model name and whole numbers.
INT_FIELDS = (
    "num_ctx",
    "prompt_tokens",
    "cached_tokens",
    "prefill_ms",
    "decode_tokens",
    "decode_ms",
    "load_ms",
    "first_token_ms",
    "total_ms",
)


def _count(value: object) -> int | None:
    """A reported non-negative whole number, else None (a missing or odd value is not a measurement)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value >= 0 else None


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _ns_to_ms(value: object) -> int | None:
    n = _count(value)
    return None if n is None else round(n / 1_000_000)


@dataclass(frozen=True)
class TurnStats:
    """What one call to the model cost. ``prompt_tokens`` counts the whole prompt, cached part included;
    ``cached_tokens`` is the part the engine reused from an earlier call instead of processing again;
    ``prefill_ms`` is the time spent on the rest. ``first_token_ms`` is measured by Anthill from sending the
    request to the first word, so it is only known on a streamed call."""

    model: str
    num_ctx: int | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    prefill_ms: int | None = None
    decode_tokens: int | None = None
    decode_ms: int | None = None
    load_ms: int | None = None
    first_token_ms: int | None = None
    total_ms: int | None = None

    @property
    def prefill_tokens(self) -> int | None:
        """Prompt tokens the engine actually processed (the prompt minus the cached part)."""
        if self.prompt_tokens is None:
            return None
        return max(self.prompt_tokens - (self.cached_tokens or 0), 0)

    @property
    def decode_tokens_per_s(self) -> float | None:
        if not self.decode_tokens or not self.decode_ms:
            return None
        return self.decode_tokens / (self.decode_ms / 1000)

    def as_dict(self) -> dict:
        return asdict(self)

    def speed_line(self) -> str | None:
        """One short line for the answer: first word and speed, whichever are known. None when neither is."""
        parts = []
        if self.first_token_ms is not None:
            ft = self.first_token_ms
            parts.append(f"first word {ft} ms" if ft < 1000 else f"first word {ft / 1000:.1f} s")
        if (rate := self.decode_tokens_per_s) is not None:
            parts.append(f"{rate:.0f} tokens/s")
        return ", ".join(parts) or None


def from_ollama(
    data: dict,
    *,
    model: str,
    num_ctx: int | None,
    first_token_ms: int | None = None,
    wall_ms: int | None = None,
) -> TurnStats:
    """Read the counters of Ollama's final ``/api/chat`` object (or the whole non-streaming reply). Durations
    arrive in nanoseconds. ``wall_ms`` (our own clock) stands in for ``total_duration`` when it is missing."""
    total_ms = _ns_to_ms(data.get("total_duration"))
    return TurnStats(
        model=model,
        num_ctx=num_ctx,
        prompt_tokens=_count(data.get("prompt_eval_count")),
        cached_tokens=_count(data.get("prompt_eval_cached_count")),
        prefill_ms=_ns_to_ms(data.get("prompt_eval_duration")),
        decode_tokens=_count(data.get("eval_count")),
        decode_ms=_ns_to_ms(data.get("eval_duration")),
        load_ms=_ns_to_ms(data.get("load_duration")),
        first_token_ms=first_token_ms,
        total_ms=total_ms if total_ms is not None else wall_ms,
    )


def from_openai(
    data: dict,
    *,
    model: str,
    first_token_ms: int | None = None,
    total_ms: int | None = None,
) -> TurnStats:
    """Read ``usage`` (and ``timings``, which llama.cpp and Ollama's ``/v1`` add) from an OpenAI-compatible
    reply. A server that sends neither yields a record with only the times Anthill measured itself."""
    usage = _dict(data.get("usage"))
    timings = _dict(data.get("timings"))
    cached = _dict(usage.get("prompt_tokens_details")).get("cached_tokens")
    return TurnStats(
        model=model,
        prompt_tokens=_count(usage.get("prompt_tokens")),
        cached_tokens=_count(cached),
        prefill_ms=_count(timings.get("prompt_ms")),
        decode_tokens=_count(usage.get("completion_tokens")),
        decode_ms=_count(timings.get("predicted_ms")),
        first_token_ms=first_token_ms,
        total_ms=total_ms,
    )
