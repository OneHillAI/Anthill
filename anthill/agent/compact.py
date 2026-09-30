"""In-session context-window compaction.

Long agent runs (and long chats) append turn after turn until they overflow the
local model's context window, which quietly wrecks its ability to reason and act.
compact_messages() keeps the system prompt and the most recent turns verbatim and
folds everything in between into one short summary, so the working context stays
inside a token budget while preserving decisions, findings, and what's left to do.

Distinct from the Memory layer: Memory is long-term, cross-session recall of
durable facts; this is in-session compaction to fit the window right now.
"""

from __future__ import annotations

from ..inference.base import Message

# Rough char->token ratio (good enough for budgeting; avoids a tokenizer dep).
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


def messages_tokens(messages) -> int:
    return sum(estimate_tokens(m.content or "") for m in messages)


_SUMMARY_SYS = (
    "Summarize the earlier conversation/agent steps below into a compact brief. "
    "Preserve: decisions made, facts and tool results found, and what still needs "
    "doing. Drop pleasantries and repetition. Be terse - a few bullet points."
)


def _summarize(transcript: str, backend, model: str | None) -> str:
    msgs = [Message("system", _SUMMARY_SYS), Message("user", transcript[:12000])]
    try:
        from ..inference.ollama import OllamaBackend

        if model and isinstance(backend, OllamaBackend):
            return backend.chat(msgs, model=model).strip()
        return backend.chat(msgs).strip()
    except Exception:
        return transcript[:1500]  # fall back to a hard truncation


def compact_messages(
    messages, backend, *, max_tokens: int = 6000, keep_recent: int = 6, model: str | None = None
):
    """Return messages compacted to fit `max_tokens`, or unchanged if already small.

    Keeps a leading system message + the last `keep_recent` messages verbatim and
    replaces the middle with a single summary message. Never raises - on a summary
    failure it truncates instead.
    """
    if messages_tokens(messages) <= max_tokens or len(messages) <= keep_recent + 2:
        return messages

    has_system = bool(messages) and messages[0].role == "system"
    head = messages[:1] if has_system else []
    recent = messages[-keep_recent:] if keep_recent > 0 else []
    middle = messages[(1 if has_system else 0) : len(messages) - len(recent)]
    if not middle:
        return messages

    transcript = "\n".join(f"{m.role}: {m.content}" for m in middle if m.content)
    summary = _summarize(transcript, backend, model)
    summ_msg = Message("system", "[Summary of earlier steps, compacted to fit context]\n" + summary)
    return [*head, summ_msg, *recent]
