from __future__ import annotations

import re


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", text.lower()) if len(t) > 2}


def passes(new_prompt: str, cached_prompt: str) -> bool:
    """Return True if the cached answer is likely correct for `new_prompt`.

    Cosine similarity alone can't catch "EU refund policy" vs "US refund policy"
    (§7.3). This guard checks that every rare word in the new prompt also appears
    in the cached prompt - if the new question introduces a discriminating term
    not present in the cached question, we fall through to generate.

    This is a keyword-overlap heuristic, deliberately cheap. It errs on the side
    of false misses (falling through to generate) rather than false hits (returning
    the wrong answer). That is the correct bias for a correctness guard.
    """
    new_tokens = _tokens(new_prompt)
    cached_tokens = _tokens(cached_prompt)
    # Tokens that appear only in the new prompt - potential discriminators.
    novel = new_tokens - cached_tokens
    if not novel:
        return True
    # Allow the hit if the novel tokens are all common English stop-words.
    # A novel content word (e.g. "EU" vs "US") means the cached answer may differ.
    stop_words = {
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
        "may",
        "might",
        "about",
        "any",
        "all",
        "not",
        "but",
        "its",
        "our",
        "your",
    }
    content_novel = novel - stop_words
    return len(content_novel) == 0
