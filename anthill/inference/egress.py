"""Egress protection for a model call that leaves this machine: redact the text, drop the images, restore the reply.

Shared by every backend that can talk to another machine (the OpenAI-compatible one, and an Ollama at a remote
address), so the rule is written once. ``anthill.hybrid.scrub`` does the redaction and is used as it is.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ..hybrid.scrub import restore, scrub
from .base import Message


def scrub_messages(messages: Sequence[Message]) -> tuple[list[Message], dict[str, str]]:
    """Redact PII from every message before it crosses the network to a remote OpenAI-compatible
    endpoint (an org's own RunPod/onprem server, or a third-party inference provider like Berget).
    Returns the scrubbed messages plus the local replacement map used to restore the reply.

    Each message is scrubbed independently - `scrub()` numbers placeholders from 1 within a single
    call - so two different messages sharing a PII kind (e.g. an email in the wiki-context message and
    a different email in the question) would otherwise both produce "[EMAIL_1]" and silently clobber
    each other's mapping when merged into one shared `replacements` dict, corrupting which original
    value a later restore() puts back into the reply (a real, reproduced bug - not a rare edge case,
    since a grounded turn routinely combines wiki context with the user's own question). Suffixing
    each message's placeholders with its index before merging keeps them unique across the batch.

    Images are ALWAYS stripped here, never sent - the redaction above only works on text (regex/NER
    pattern matching over `scrub()`), so there is no honest way to "scrub" pixel content out of an
    arbitrary image. Failing closed (drop it) matches this same file's existing local-vision path
    being unaffected (the on-device Ollama backend never reaches this function - `stays_local()` skips
    scrubbing entirely, so nothing changes there) and the codebase's established fail-closed precedent
    for anything that can't be safely protected (see `hybrid/escalate.py`'s consent-gated design). A
    short note replaces the image in the text so the model - and the user, via its reply - knows why
    the image wasn't seen, instead of silently answering as if it never existed.
    """
    replacements: dict[str, str] = {}
    scrubbed: list[Message] = []
    for i, m in enumerate(messages):
        sr = scrub(m.content)
        text = sr.text
        for ph, original in sr.replacements.items():
            unique_ph = f"{ph[:-1]}_m{i}]"  # "[EMAIL_1]" -> "[EMAIL_1_m0]"
            text = text.replace(ph, unique_ph)
            replacements[unique_ph] = original
        if m.images:
            n = len(m.images)
            noun = "image" if n == 1 else "images"
            note = (
                f"[{n} attached {noun} not sent - this connected model does not receive images, to "
                "keep them from leaving this machine. Tell the user to switch to a local model for "
                "image analysis.]"
            )
            text = f"{text}\n\n{note}" if text else note
        scrubbed.append(Message(m.role, text, None))
    return scrubbed, replacements


def restore_stream(tokens: Iterator[str], replacements: dict[str, str]) -> Iterator[str]:
    """Restore placeholders in a live token stream without ever emitting a broken/partial one -
    holds back text from the last unmatched '[' until it closes, then restores and flushes."""
    if not replacements:
        yield from tokens
        return
    buf = ""
    for tok in tokens:
        buf += tok
        cut = len(buf)
        last_open = buf.rfind("[")
        if last_open != -1 and "]" not in buf[last_open:]:
            cut = last_open
        if cut > 0:
            yield restore(buf[:cut], replacements)
            buf = buf[cut:]
    if buf:
        yield restore(buf, replacements)
